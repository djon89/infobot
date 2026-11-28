import asyncio
import logging
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from sqlalchemy import select

from app import digest as dg
from app import posts as pst
from app.db import subs_repo as sr
from app.db.models import Channel, DigestSettings, GoogleAccount, Post, UserChannel, utcnow
from app.services.blurbs import make_blurbs
from app.services.feeds import FeedError, channel_url_for_video
from app.services.google_yt import GoogleAuthError, GoogleYouTube, Pending
from app.telegram_send import send_or_edit_rich

log = logging.getLogger(__name__)


async def _edit(bot: Bot, chat_id: int, msg_id: int, text: str, reply_markup=None):
    try:
        await bot.edit_message_text(text, chat_id=chat_id, message_id=msg_id, reply_markup=reply_markup,
                                    disable_web_page_preview=True)
    except TelegramBadRequest:
        pass


def _aware(dt):
    return dt if dt is None or dt.tzinfo else dt.replace(tzinfo=timezone.utc)


# ---------- умная сводка: строка «что внутри» ----------

async def fill_blurbs(ctx, user_id: int, items) -> dict[str, str]:
    """Строки для видео сводки: готовые из кэша, недостающие — одним запросом к модели.
    Если модель недоступна — сводка просто без строк."""
    sm = ctx["sessionmaker"]
    norm = [it if isinstance(it, dict) else {"id": it[0].video_id, "title": it[0].title, "channel": it[1].title}
            for it in items]
    ids = [it["id"] for it in norm]
    async with sm() as db:
        known = await sr.blurbs_for(db, ids)
    lines = {vid: b.blurb for vid, b in known.items() if b.blurb}
    missing = [{"id": it["id"], "title": it["title"], "channel": it["channel"],
                "description": known[it["id"]].description if it["id"] in known else ""}
               for it in norm if it["id"] not in lines][:60]
    if missing:
        try:
            new = await make_blurbs(ctx["llm"].for_user(user_id), missing)
            async with sm() as db:
                await sr.save_blurbs(db, new)
                await db.commit()
            lines.update(new)
        except Exception as e:  # noqa: BLE001
            log.warning("blurbs failed: %s", e)
    return lines


# ---------- опрос лент ----------

async def poll_channels(ctx, channel_ids: list[str]) -> int:
    sm, feeds = ctx["sessionmaker"], ctx["feeds"]
    sem = asyncio.Semaphore(5)
    total = 0

    async def one(cid: str):
        nonlocal total
        async with sem:
            err, entries = None, []
            try:
                entries = await feeds.uploads(cid)
            except Exception as e:  # noqa: BLE001
                err = str(e)[:500]
                log.warning("feed %s failed: %s", cid, e)
            async with sm() as db:
                total += await sr.save_entries(db, entries)
                ch = await db.get(Channel, cid)
                if ch:
                    ch.checked_at, ch.last_error = utcnow(), err
                await db.commit()

    await asyncio.gather(*(one(c) for c in channel_ids))
    return total


POLL_EVERY = timedelta(minutes=55)
POLL_BATCH = 400


async def poll_feeds(ctx):
    """Каналы, которые давно не проверялись, — порциями: нагрузка на YouTube ровная при любом их числе."""
    async with ctx["sessionmaker"]() as db:
        ids = await sr.channels_to_poll(db)
        rows = (await db.execute(select(Channel.id, Channel.checked_at).where(Channel.id.in_(ids)))).all() if ids else []
    cutoff = utcnow() - POLL_EVERY
    stale = sorted((r for r in rows if not r.checked_at or _aware(r.checked_at) < cutoff),
                   key=lambda r: _aware(r.checked_at) or datetime.min.replace(tzinfo=timezone.utc))
    batch = [r.id for r in stale[:POLL_BATCH]]
    if batch:
        n = await poll_channels(ctx, batch)
        log.info("polled %d/%d channels, %d new videos", len(batch), len(ids), n)


# ---------- подписка на канал ----------

async def channel_page(ctx, channel_id: str, offset: int) -> tuple[list[dict], bool]:
    """Видео канала [offset, offset+5) от новых к старым. Первые ~15 — из RSS (с датами и описаниями),
    дальше — через yt-dlp. Возвращает (items, есть_ещё)."""
    n = pst.CHANNEL_PAGE
    entries = await ctx["feeds"].uploads(channel_id)
    async with ctx["sessionmaker"]() as db:
        ch = await db.get(Channel, channel_id)
        if ch is None:
            ch = Channel(id=channel_id, title=channel_id)
            db.add(ch)
            await db.flush()
        await sr.save_entries(db, entries)
        await db.commit()
    title = ch.title if ch else channel_id
    if offset + n <= len(entries) or len(entries) < 15:
        page = entries[offset:offset + n]
        items = [pst.item(e.video_id, e.title, title, e.published) for e in page]
        return items, offset + n < len(entries) or len(entries) >= 15
    got = await ctx["downloader"].uploads(channel_id, offset, n)
    return [pst.item(e["id"], e["title"], title, None, e.get("duration")) for e in got], len(got) == n


async def show_channel(ctx, *, user_id: int, chat_id: int, channel_id: str, offset: int = 0,
                       message_id: int | None = None, channel_description: str = "", head_text: str = ""):
    """Пост «страница канала»: 5 видео с обложками, кнопки, «Ещё 5 видео»."""
    bot, sm = ctx["bot"], ctx["sessionmaker"]
    try:
        items, has_more = await channel_page(ctx, channel_id, offset)
    except Exception:  # noqa: BLE001
        log.exception("channel page %s/%s failed", channel_id, offset)
        if message_id:
            await _edit(bot, chat_id, message_id, "❌ Не удалось загрузить видео канала.")
        return
    lines = await fill_blurbs(ctx, user_id, items)
    for it in items:
        it["blurb"] = lines.get(it["id"])
    async with sm() as db:
        ch = await db.get(Channel, channel_id)
        followed = await db.get(UserChannel, (user_id, channel_id)) is not None
        post = Post(user_id=user_id, kind="channel", items=items, meta={
            "channel_id": channel_id, "channel_title": ch.title if ch else channel_id, "offset": offset,
            "has_more": has_more, "followed": followed, "channel_description": channel_description})
        db.add(post)
        await db.commit()
    if not items:
        text = (head_text + "\n\n" if head_text else "") + "У канала нет обычных видео (только Shorts или пусто)."
        if message_id:
            await _edit(bot, chat_id, message_id, text)
        else:
            await bot.send_message(chat_id, text)
        return
    if head_text and message_id:
        await _edit(bot, chat_id, message_id, head_text)
        message_id = None  # заголовок остаётся отдельным сообщением, пост — новым
    await send_or_edit_rich(bot, chat_id, pst.blocks(post), pst.as_html(post), pst.keyboard(post),
                            edit_message_id=message_id)


async def browse_channel(ctx, *, user_id: int, chat_id: int, channel_id: str, offset: int, message_id: int):
    await show_channel(ctx, user_id=user_id, chat_id=chat_id, channel_id=channel_id, offset=offset,
                       message_id=message_id)


async def follow_channel(ctx, *, user_id: int, chat_id: int, channel_url: str | None = None,
                         video_id: str | None = None, channel_id: str | None = None,
                         status_message_id: int, follow: bool = True):
    bot, sm, s = ctx["bot"], ctx["sessionmaker"], ctx["settings"]
    try:
        if channel_id:
            async with sm() as db:
                ch = await db.get(Channel, channel_id)
            from app.services.feeds import ChannelInfo
            info = ChannelInfo(channel_id, ch.title if ch else channel_id)
        else:
            if not channel_url:
                channel_url = await channel_url_for_video(video_id, s.generic_proxy_url or None)
            info = await ctx["feeds"].resolve(channel_url)
        async with sm() as db:
            is_new = await sr.follow(db, user_id, info.id, info.title) if follow else False
            await db.commit()
    except FeedError as e:
        await _edit(bot, chat_id, status_message_id, f"❌ {e}")
        return
    except Exception:  # noqa: BLE001
        log.exception("follow failed")
        await _edit(bot, chat_id, status_message_id, "❌ Не удалось подписаться на канал.")
        return
    head = ("✅ Слежу за каналом" if is_new else "Вы уже следите за каналом") + f" «{info.title}».\n"
    head += "Новые полноценные видео (без Shorts) будут приходить в ежедневной сводке. /channels — список."
    await show_channel(ctx, user_id=user_id, chat_id=chat_id, channel_id=info.id, message_id=status_message_id,
                       channel_description=info.description, head_text=head)


# ---------- сводка ----------

async def make_digest(ctx, *, user_id: int, chat_id: int, manual: bool = True,
                      status_message_id: int | None = None) -> bool:
    bot, sm, s = ctx["bot"], ctx["sessionmaker"], ctx["settings"]
    async with sm() as db:
        ds = await sr.get_digest_settings(db, user_id, s.default_tz, s.default_digest_hour)
        await db.commit()
        channel_ids = await sr.channels_to_poll(db, user_id)
    if not channel_ids:
        if manual:
            await _send(bot, chat_id, status_message_id, dg.render_channels([], 0)[0])
        return False

    # свежие данные по каналам, которые давно не проверялись
    async with sm() as db:
        stale_before = utcnow() - timedelta(minutes=45)
        rows = (await db.execute(select(Channel).where(Channel.id.in_(channel_ids)))).scalars()
        stale = [c.id for c in rows if not c.checked_at or _aware(c.checked_at) < stale_before]
    if stale:
        await poll_channels(ctx, stale)

    now = utcnow()
    since = _aware(ds.cursor) or now - timedelta(days=1)
    async with sm() as db:
        items = await sr.new_videos_for(db, user_id, since)
        ds = await db.get(DigestSettings, user_id)
        ds.cursor = now
        await db.commit()
    if not items:
        if manual:
            await _send(bot, chat_id, status_message_id,
                        f"Новых видео нет (с {since.astimezone(ZoneInfo(ds.tz)):%d.%m %H:%M}). "
                        f"Слежу за каналами: {len(channel_ids)}.")
        return False
    if status_message_id:
        await _edit(bot, chat_id, status_message_id, f"✍️ Смотрю, что внутри {len(items)} новых видео…")
    lines = await fill_blurbs(ctx, user_id, items)
    async with sm() as db:
        post = Post(user_id=user_id, kind="digest", items=[
            pst.item(v.video_id, v.title, c.title, _aware(v.published_at), blurb=lines.get(v.video_id))
            for v, c in items], meta={})
        db.add(post)
        await db.commit()
    if status_message_id:
        try:
            await bot.delete_message(chat_id, status_message_id)
        except TelegramBadRequest:
            pass
    await send_or_edit_rich(bot, chat_id, pst.blocks(post), pst.as_html(post), pst.keyboard(post))
    return True


async def _send(bot: Bot, chat_id: int, status_message_id: int | None, text: str, kb=None):
    if status_message_id:
        await _edit(bot, chat_id, status_message_id, text, kb)
    else:
        await bot.send_message(chat_id, text, reply_markup=kb, disable_web_page_preview=True)


async def send_due_digests(ctx):
    """Раз в час: отправить сводку тем, у кого сейчас наступил выбранный час."""
    sm = ctx["sessionmaker"]
    async with sm() as db:
        rows = list((await db.execute(select(DigestSettings).where(DigestSettings.hour.is_not(None)))).scalars())
    for ds in rows:
        local = datetime.now(ZoneInfo(ds.tz))
        today = local.strftime("%Y-%m-%d")
        if local.hour != ds.hour or ds.last_sent_on == today:
            continue
        try:
            await make_digest(ctx, user_id=ds.user_id, chat_id=ds.user_id, manual=False)
        except TelegramForbiddenError:
            log.info("user %s blocked the bot", ds.user_id)
        except Exception:  # noqa: BLE001
            log.exception("digest for %s failed", ds.user_id)
        async with sm() as db:
            (await db.get(DigestSettings, ds.user_id)).last_sent_on = today
            await db.commit()


# ---------- подписки YouTube-аккаунта ----------

def _google(ctx) -> GoogleYouTube | None:
    s = ctx["settings"]
    if not (s.google_client_id and s.google_client_secret):
        return None
    return ctx.get("google") or GoogleYouTube(s.google_client_id, s.google_client_secret)


async def _apply_subscriptions(ctx, user_id: int, subs: list[tuple[str, str]]) -> tuple[int, int]:
    """Добавить новые подписки, убрать те, от которых отписались на YouTube. (added, removed)"""
    sm = ctx["sessionmaker"]
    added = removed = 0
    ids = {cid for cid, _ in subs}
    async with sm() as db:
        for cid, title in subs:
            added += await sr.follow(db, user_id, cid, title, source="youtube")
        old = (await db.execute(select(UserChannel).where(UserChannel.user_id == user_id,
                                                          UserChannel.source == "youtube"))).scalars()
        for uc in list(old):
            if uc.channel_id not in ids:
                await db.delete(uc)
                removed += 1
        await db.commit()
    return added, removed


async def import_youtube(ctx, *, user_id: int, chat_id: int, status_message_id: int):
    bot, sm = ctx["bot"], ctx["sessionmaker"]
    g = _google(ctx)
    if g is None:
        await _edit(bot, chat_id, status_message_id,
                    "Импорт подписок YouTube ещё не настроен владельцем бота (нет ключей Google).")
        return
    try:
        flow = await g.start_device_flow()
        url = flow.get("verification_url") or flow.get("verification_uri") or "https://www.google.com/device"
        await _edit(bot, chat_id, status_message_id,
                    f"🔑 Откройте <b>{url}</b>, войдите в нужный Google-аккаунт и введите код:\n\n"
                    f"<code>{flow['user_code']}</code>\n\n"
                    "Бот получит доступ только на чтение подписок. Код действует "
                    f"{int(flow.get('expires_in', 1800)) // 60} минут.")
        interval = int(flow.get("interval", 5))
        deadline = asyncio.get_running_loop().time() + int(flow.get("expires_in", 1800))
        token = None
        while asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(interval)
            try:
                token = await g.poll_token(flow["device_code"])
                break
            except Pending as p:
                if p.slow_down:
                    interval += 5
        if not token:
            raise GoogleAuthError("Код истёк — запустите /import ещё раз.")
        await _edit(bot, chat_id, status_message_id, "⏳ Загружаю ваши подписки…")
        subs = await g.subscriptions(token["access_token"])
        if token.get("refresh_token"):
            async with sm() as db:
                ga = await db.get(GoogleAccount, user_id)
                if ga:
                    ga.refresh_token = token["refresh_token"]
                else:
                    db.add(GoogleAccount(user_id=user_id, refresh_token=token["refresh_token"]))
                await db.commit()
        added, _ = await _apply_subscriptions(ctx, user_id, subs)
        async with sm() as db:
            (await db.get(GoogleAccount, user_id)).synced_at = utcnow() if token.get("refresh_token") else None
            await db.commit()
    except GoogleAuthError as e:
        await _edit(bot, chat_id, status_message_id, f"❌ {e}")
        return
    except Exception:  # noqa: BLE001
        log.exception("youtube import failed")
        await _edit(bot, chat_id, status_message_id, "❌ Не удалось импортировать подписки.")
        return
    await _edit(bot, chat_id, status_message_id,
                f"✅ Подписок на YouTube: {len(subs)}, новых в боте: {added}.\n"
                "Раз в день бот сам сверяет подписки с YouTube. Новые видео — в ежедневной сводке, "
                "сейчас — /digest. Список — /channels.")
    await poll_channels(ctx, [cid for cid, _ in subs])


async def sync_youtube_subs(ctx):
    g = _google(ctx)
    if g is None:
        return
    sm = ctx["sessionmaker"]
    async with sm() as db:
        accounts = list((await db.execute(select(GoogleAccount))).scalars())
    for ga in accounts:
        try:
            subs = await g.subscriptions(await g.refresh(ga.refresh_token))
            added, removed = await _apply_subscriptions(ctx, ga.user_id, subs)
            async with sm() as db:
                (await db.get(GoogleAccount, ga.user_id)).synced_at = utcnow()
                await db.commit()
            log.info("synced youtube subs for %s: +%d -%d", ga.user_id, added, removed)
        except Exception:  # noqa: BLE001
            log.exception("sync youtube subs for %s failed", ga.user_id)
