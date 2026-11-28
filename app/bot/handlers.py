import html
import json

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import CallbackQuery, Message
from arq.connections import ArqRedis

from app.bot import keyboards as kb
from app.config import DOWNLOAD_QUEUE, Settings
from app import digest as dg
from app.db import repo
from app.db import subs_repo as sr
from app import limits, render
from app import posts as pst
from app.db.models import Digest, Post, Video
from app.services.summarizer import load_summary
from app.services.youtube import VideoMeta
from app.stats import build_stats
from app.telegram_send import send_article, send_or_edit_rich
from app.services.downloader import AUDIO_DEFAULTS, QUALITY_LABELS
from app.services.feeds import extract_channel_ref
from app.services.youtube import extract_video_ids

router = Router()

HELP = (
    "Пришлите ссылку на YouTube-видео — я верну:\n"
    "• 📝 конспект с ключевыми мыслями;\n"
    "• 📄 полную расшифровку файлом;\n"
    "• ⬇️ кнопку, чтобы скачать само видео или звук.\n\n"
    "Пришлите ссылку на канал — буду следить за новыми видео (без Shorts) "
    "и раз в день присылать одну сводку.\n\n"
    "/digest — что нового на каналах прямо сейчас\n"
    "/channels — за кем я слежу\n"
    "/import — взять каналы из подписок вашего YouTube\n"
    "/settings — качество, аудиодорожка, время сводки"
)


@router.message(CommandStart())
async def start(m: Message):
    await m.answer("Привет! " + HELP + f"\n\nВаш ID: <code>{m.from_user.id}</code>")


@router.message(Command("help"))
async def help_(m: Message):
    from app.changelog import VERSION
    await m.answer(HELP + f"\n\nВерсия бота: {VERSION}")




# ---------- настройки ----------

async def _settings_view(s, settings: Settings, user_id: int):
    us = await repo.get_user_settings(s, user_id, settings.default_quality, settings.default_audio)
    ds = await sr.get_digest_settings(s, user_id, settings.default_tz, settings.default_digest_hour)
    usage = await limits.usage_text(s, settings, user_id)
    return (kb.settings_text(us.quality, us.audio, ds.hour, ds.tz) + f"\n\n📊 {usage}",
            kb.settings_kb(us.quality, us.audio, ds.hour))


@router.message(Command("settings"))
async def settings_cmd(m: Message, settings: Settings, sessionmaker):
    async with sessionmaker() as s:
        await repo.upsert_user(s, m.from_user)
        text, markup = await _settings_view(s, settings, m.from_user.id)
        await s.commit()
    await m.answer(text, reply_markup=markup)


@router.callback_query(F.data.startswith("set:"))
async def settings_cb(c: CallbackQuery, settings: Settings, sessionmaker):
    _, what, val = c.data.split(":", 2)
    valid = {"q": QUALITY_LABELS, "a": AUDIO_DEFAULTS,
             "h": {str(h) for h in kb.DIGEST_HOURS} | {"off"}}
    if what not in valid or val not in valid[what]:
        await c.answer()
        return
    async with sessionmaker() as s:
        await repo.upsert_user(s, c.from_user)
        if what in ("q", "a"):
            await repo.set_user_setting(s, c.from_user.id, settings.default_quality, settings.default_audio,
                                        quality=val if what == "q" else None, audio=val if what == "a" else None)
        else:
            ds = await sr.get_digest_settings(s, c.from_user.id, settings.default_tz, settings.default_digest_hour)
            ds.hour = None if val == "off" else int(val)
        await s.flush()
        text, markup = await _settings_view(s, settings, c.from_user.id)
        await s.commit()
    await c.message.edit_text(text, reply_markup=markup)
    await c.answer("Сохранено")


# ---------- скачивание ----------

class Origin:
    """Откуда пришло действие: кнопка или команда /sum_…, /dl_… — дальше обрабатываются одинаково."""

    def __init__(self, user, chat_id: int, reply, alert, ack):
        self.user, self.chat_id, self.reply, self.alert, self.ack = user, chat_id, reply, alert, ack

    @classmethod
    def from_cb(cls, c: CallbackQuery):
        async def alert(t): await c.answer(t, show_alert=True)
        async def ack(t): await c.answer(t)
        return cls(c.from_user, c.message.chat.id, c.message.answer, alert, ack)

    @classmethod
    def from_msg(cls, m: Message):
        async def ack(t): pass
        return cls(m.from_user, m.chat.id, m.answer, m.answer, ack)


async def _enqueue_download(o, settings: Settings, sessionmaker, arq: ArqRedis,
                            video_id: str, quality: str, audio: str):
    if not isinstance(o, Origin):
        o = Origin.from_cb(o)
    async with sessionmaker() as s:
        await repo.upsert_user(s, o.user)
        if quality == "d" or audio == "d":
            us = await repo.get_user_settings(s, o.user.id, settings.default_quality, settings.default_audio)
            quality = us.quality if quality == "d" else quality
            audio = us.audio if audio == "d" else audio
        if err := await limits.check(s, settings, o.user.id, "download"):
            await s.commit()
            await o.alert(err)
            return
        job = await repo.create_job(s, o.user.id, f"{video_id}:{quality}:{audio}", kind="youtube_video")
        await s.commit()
    await o.ack("Скачиваю…")
    status = await o.reply(f"⏳ Скачивание в очереди ({QUALITY_LABELS.get(quality, quality)})…")
    await arq.enqueue_job(
        "download_video", _queue_name=DOWNLOAD_QUEUE, job_id=job.id, user_id=o.user.id, chat_id=o.chat_id,
        video_id=video_id, quality=quality, audio=audio, status_message_id=status.message_id,
    )


async def _enqueue_summary(o: Origin, settings: Settings, sessionmaker, arq: ArqRedis, vid: str):
    async with sessionmaker() as s:
        await repo.upsert_user(s, o.user)
        if err := await limits.check(s, settings, o.user.id, "summary"):
            await s.commit()
            await o.alert(err)
            return
        job = await repo.create_job(s, o.user.id, vid)
        await s.commit()
    await o.ack("Делаю конспект…")
    status = await o.reply(f"⏳ Конспект: https://youtu.be/{vid}", disable_web_page_preview=True)
    await arq.enqueue_job("process_youtube", job_id=job.id, user_id=o.user.id, chat_id=o.chat_id,
                          video_id=vid, status_message_id=status.message_id, reply_to=status.message_id)


@router.callback_query(F.data.startswith("dl:"))
async def download_cb(c: CallbackQuery, settings: Settings, sessionmaker, arq: ArqRedis):
    _, vid, quality, audio = c.data.split(":", 3)
    await _enqueue_download(c, settings, sessionmaker, arq, vid, quality, audio)


@router.callback_query(F.data.startswith("pick:"))
async def pick_cb(c: CallbackQuery, arq: ArqRedis):
    vid = c.data.split(":", 1)[1]
    await c.answer("Смотрю, какие есть варианты…")
    status = await c.message.answer("🔎 Смотрю доступные качества…")
    await arq.enqueue_job("probe_video", _queue_name=DOWNLOAD_QUEUE, chat_id=c.message.chat.id, video_id=vid,
                          status_message_id=status.message_id)


@router.callback_query(F.data.startswith("q:"))
async def quality_cb(c: CallbackQuery, settings: Settings, sessionmaker, arq: ArqRedis):
    _, vid, quality = c.data.split(":", 2)
    raw = await arq.get(f"probe:{vid}")
    probe = json.loads(raw) if raw else {}
    size = probe.get("sizes", {}).get(quality, 0)
    if size > settings.max_upload_bytes:
        await c.answer("Это качество больше лимита Telegram — выберите пониже.", show_alert=True)
        return
    tracks = probe.get("audio_tracks", [])
    if len(tracks) > 1:
        from app.services.downloader import AudioTrack
        await c.message.edit_text(
            f"🎧 Выберите аудиодорожку ({QUALITY_LABELS.get(quality, quality)}):",
            reply_markup=kb.audio_picker(vid, quality, [AudioTrack(**t) for t in tracks]))
        await c.answer()
        return
    await _enqueue_download(c, settings, sessionmaker, arq, vid, quality, "orig")


# ---------- каналы и сводки ----------

@router.callback_query(F.data == "noop")
async def noop_cb(c: CallbackQuery):
    await c.answer()


async def _start_follow(m_or_c, arq: ArqRedis, sessionmaker, user, chat_id: int, **kw):
    async with sessionmaker() as s:
        await repo.upsert_user(s, user)
        await s.commit()
    target = m_or_c.message if isinstance(m_or_c, CallbackQuery) else m_or_c
    status = await target.answer("🔎 Ищу канал…")
    await arq.enqueue_job("follow_channel", user_id=user.id, chat_id=chat_id,
                          status_message_id=status.message_id, **kw)


@router.callback_query(F.data.startswith("fv:"))
async def follow_from_video_cb(c: CallbackQuery, sessionmaker, arq: ArqRedis):
    await c.answer()
    await _start_follow(c, arq, sessionmaker, c.from_user, c.message.chat.id, video_id=c.data.split(":", 1)[1])


@router.message(Command("follow"))
async def follow_cmd(m: Message, sessionmaker, arq: ArqRedis):
    ref = extract_channel_ref(m.text or "")
    if not ref:
        await m.answer("Пришлите ссылку на канал, например: <code>/follow https://www.youtube.com/@channel</code>")
        return
    await _start_follow(m, arq, sessionmaker, m.from_user, m.chat.id, channel_url=ref)


@router.message(Command("channels"))
async def channels_cmd(m: Message, sessionmaker):
    async with sessionmaker() as s:
        await repo.upsert_user(s, m.from_user)
        chans = await sr.user_channels(s, m.from_user.id)
        await s.commit()
    text, markup = dg.render_channels(chans, 0)
    await m.answer(text, reply_markup=markup)


@router.callback_query(F.data.startswith("chp:") | F.data.startswith("uf:"))
async def channels_page_cb(c: CallbackQuery, sessionmaker):
    parts = c.data.split(":")
    async with sessionmaker() as s:
        if parts[0] == "uf":
            await sr.unfollow(s, c.from_user.id, parts[1])
            await s.commit()
        chans = await sr.user_channels(s, c.from_user.id)
    page = int(parts[-1]) if parts[-1].lstrip("-").isdigit() else 0
    text, markup = dg.render_channels(chans, page)
    await c.message.edit_text(text, reply_markup=markup)
    await c.answer("Отписался" if parts[0] == "uf" else None)


@router.message(Command("digest"))
async def digest_cmd(m: Message, sessionmaker, arq: ArqRedis):
    async with sessionmaker() as s:
        await repo.upsert_user(s, m.from_user)
        await s.commit()
    status = await m.answer("🔎 Проверяю каналы…")
    await arq.enqueue_job("make_digest", user_id=m.from_user.id, chat_id=m.chat.id, manual=True,
                          status_message_id=status.message_id)


@router.callback_query(F.data.startswith("dg:"))
async def digest_page_cb(c: CallbackQuery, sessionmaker):
    _, did, page = c.data.split(":")
    async with sessionmaker() as s:
        d = await s.get(Digest, int(did))
        if not d or d.user_id != c.from_user.id:
            await c.answer("Сводка устарела", show_alert=True)
            return
        items = await sr.digest_items(s, d)
        bl = await sr.blurbs_for(s, [v.video_id for v, _ in items])
    text, markup = dg.render(items, int(page), d.id, blurbs={k: b.blurb for k, b in bl.items() if b.blurb})
    await c.message.edit_text(text, reply_markup=markup, disable_web_page_preview=True)
    await c.answer()


@router.callback_query(F.data.startswith("s:"))
async def summary_cb(c: CallbackQuery, settings: Settings, sessionmaker, arq: ArqRedis):
    await _enqueue_summary(Origin.from_cb(c), settings, sessionmaker, arq, c.data.split(":", 1)[1])


# ---------- посты со списком видео: команды, листание, каналы ----------

@router.message(F.text.regexp(r"^/(sum|dl)_\d+_\d+"))
async def post_command(m: Message, settings: Settings, sessionmaker, arq: ArqRedis):
    action, pid, n = pst.parse_command(m.text)
    async with sessionmaker() as s:
        post = await s.get(Post, pid)
    if not post or post.user_id != m.from_user.id or not 1 <= n <= len(post.items):
        await m.answer("Не нашёл это видео — возможно, команда из чужого или старого списка.")
        return
    vid = post.items[n - 1]["id"]
    o = Origin.from_msg(m)
    if action == "sum":
        await _enqueue_summary(o, settings, sessionmaker, arq, vid)
    else:
        await _enqueue_download(o, settings, sessionmaker, arq, vid, "d", "d")


@router.callback_query(F.data.startswith("pg:"))
async def post_page_cb(c: CallbackQuery, sessionmaker):
    _, pid, page = c.data.split(":")
    async with sessionmaker() as s:
        post = await s.get(Post, int(pid))
    if not post or post.user_id != c.from_user.id:
        await c.answer("Список устарел", show_alert=True)
        return
    await c.answer()
    await send_or_edit_rich(c.bot, c.message.chat.id, pst.blocks(post, int(page)), pst.as_html(post, int(page)),
                            pst.keyboard(post, int(page)), edit_message_id=c.message.message_id)


@router.callback_query(F.data.startswith("chb:"))
async def channel_more_cb(c: CallbackQuery, arq: ArqRedis):
    _, cid, offset, _pid = c.data.split(":")
    await c.answer("Загружаю видео…")
    await arq.enqueue_job("browse_channel", user_id=c.from_user.id, chat_id=c.message.chat.id,
                          channel_id=cid, offset=int(offset), message_id=c.message.message_id)


@router.callback_query(F.data.startswith("chv:"))
async def channel_view_cb(c: CallbackQuery, arq: ArqRedis):
    await c.answer("Загружаю видео канала…")
    await arq.enqueue_job("browse_channel", user_id=c.from_user.id, chat_id=c.message.chat.id,
                          channel_id=c.data.split(":", 1)[1], offset=0, message_id=None)


@router.callback_query(F.data.startswith("fc:"))
async def follow_from_post_cb(c: CallbackQuery, sessionmaker):
    _, cid, pid = c.data.split(":")
    async with sessionmaker() as s:
        await repo.upsert_user(s, c.from_user)
        post = await s.get(Post, int(pid))
        title = (post.meta or {}).get("channel_title", cid) if post else cid
        await sr.follow(s, c.from_user.id, cid, title)
        if post and post.user_id == c.from_user.id:
            post.meta = {**(post.meta or {}), "followed": True}
        await s.commit()
    await c.answer(f"✅ Слежу за «{title}»")
    if post:
        try:
            await c.message.edit_reply_markup(reply_markup=pst.keyboard(post))
        except Exception:  # noqa: BLE001
            pass


@router.message(Command("import"))
async def import_cmd(m: Message, sessionmaker, arq: ArqRedis):
    async with sessionmaker() as s:
        await repo.upsert_user(s, m.from_user)
        await s.commit()
    status = await m.answer("⏳ Запрашиваю код у Google…")
    await arq.enqueue_job("import_youtube", user_id=m.from_user.id, chat_id=m.chat.id,
                          status_message_id=status.message_id)


# ---------- конспект: коротко / подробно о части ----------

async def _load_summary(sessionmaker, vid: str):
    async with sessionmaker() as s:
        v = await s.get(Video, vid)
    d = load_summary(v.summary if v else None)
    return (VideoMeta(vid, v.title, v.author), d) if d else (None, None)


@router.callback_query(F.data.startswith("sh:"))
async def short_cb(c: CallbackQuery, sessionmaker):
    vid = c.data.split(":", 1)[1]
    meta, d = await _load_summary(sessionmaker, vid)
    if not d:
        await c.answer("Конспект устарел — пришлите ссылку ещё раз.", show_alert=True)
        return
    await c.answer()
    await send_article(c.bot, c.message.chat.id, render.short_blocks(meta, d), render.short_html(meta, d),
                       reply_to=c.message.message_id)


@router.callback_query(F.data.startswith("dp:"))
async def sections_cb(c: CallbackQuery, sessionmaker):
    vid = c.data.split(":", 1)[1]
    meta, d = await _load_summary(sessionmaker, vid)
    if not d:
        await c.answer("Конспект устарел — пришлите ссылку ещё раз.", show_alert=True)
        return
    await c.answer()
    await c.message.answer(f"🔍 Какую часть «{html.escape(meta.title)}» разобрать подробно?",
                           reply_markup=kb.sections_picker(vid, d["sections"]))


@router.callback_query(F.data.startswith("dd:"))
async def deep_cb(c: CallbackQuery, settings: Settings, sessionmaker, arq: ArqRedis):
    _, vid, idx = c.data.split(":")
    async with sessionmaker() as s:
        await repo.upsert_user(s, c.from_user)
        if err := await limits.check(s, settings, c.from_user.id, "summary"):
            await s.commit()
            await c.answer(err, show_alert=True)
            return
        job = await repo.create_job(s, c.from_user.id, f"{vid}:{idx}", kind="deep_dive")
        await s.commit()
    await c.answer("Разбираю…")
    status = await c.message.answer("⏳ Подробный разбор в очереди…")
    await arq.enqueue_job("deep_dive", job_id=job.id, user_id=c.from_user.id, chat_id=c.message.chat.id,
                          video_id=vid, idx=int(idx), status_message_id=status.message_id)


@router.message(Command("stats"))
async def stats_cmd(m: Message, settings: Settings, sessionmaker):
    if m.from_user.id not in settings.owner_ids:
        await m.answer("Статистика доступна только владельцу бота.")
        return
    async with sessionmaker() as s:
        await m.answer(await build_stats(s))


# ---------- ссылки (последним: ловит любой текст) ----------

@router.message(F.text | F.caption)
async def on_text(m: Message, settings: Settings, sessionmaker, arq: ArqRedis):
    text = m.text or m.caption or ""
    ids = extract_video_ids(text)
    if not ids:
        ref = extract_channel_ref(text)
        if ref:
            await _start_follow(m, arq, sessionmaker, m.from_user, m.chat.id, channel_url=ref)
            return
        await m.answer("Не нашёл ссылку на YouTube. " + HELP)
        return
    ids = ids[:3]  # не больше трёх видео за одно сообщение
    user = m.from_user

    async with sessionmaker() as s:
        await repo.upsert_user(s, user)
        if err := await limits.check(s, settings, user.id, "summary", len(ids)):
            await s.commit()
            await m.answer(err)
            return
        jobs = [await repo.create_job(s, user.id, vid) for vid in ids]
        await s.commit()

    for job, vid in zip(jobs, ids):
        status = await m.reply("⏳ В очереди…")
        await arq.enqueue_job(
            "process_youtube",
            job_id=job.id, user_id=user.id, chat_id=m.chat.id,
            video_id=vid, status_message_id=status.message_id,
            reply_to=m.message_id,
        )
