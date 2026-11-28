from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import event, select

from app import digest as dg
from app.config import Settings
from app.db import repo
from app.db import subs_repo as sr
from app.db.models import DigestSettings, GoogleAccount, UserChannel
from app.db.session import init_db, make_engine, make_sessionmaker
from app.services.feeds import ChannelInfo, FeedEntry, extract_channel_ref, parse_channel_page, parse_feed
from app.services.google_yt import Pending
from app.worker import subs_tasks as st

CID = "UC" + "a" * 22
FEED = f"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns:yt="http://www.youtube.com/xml/schemas/2015" xmlns="http://www.w3.org/2005/Atom">
 <title>Videos</title><author><name>Канал Тест</name></author>
 <entry><yt:videoId>vid00000001</yt:videoId><yt:channelId>{CID}</yt:channelId>
  <title>Первое &amp; видео</title><published>2026-11-20T10:00:00+00:00</published></entry>
 <entry><yt:videoId>vid00000002</yt:videoId><yt:channelId>{CID}</yt:channelId>
  <title>Второе</title><published>2026-11-21T10:00:00+00:00</published></entry>
</feed>"""


def test_channel_refs():
    assert extract_channel_ref("смотри https://www.youtube.com/@Veritasium/videos") == "https://www.youtube.com/@Veritasium"
    assert extract_channel_ref(f"youtube.com/channel/{CID}") == f"https://www.youtube.com/channel/{CID}"
    assert extract_channel_ref("https://youtube.com/c/Foo") == "https://www.youtube.com/c/Foo"
    assert extract_channel_ref("https://youtu.be/dQw4w9WgXcQ") is None


def test_parse_feed_and_page():
    es = parse_feed(FEED, CID)
    assert [e.video_id for e in es] == ["vid00000001", "vid00000002"]
    assert es[0].title == "Первое & видео" and es[0].published.tzinfo
    html = f'<meta property="og:title" content="Канал &amp; Ко"><meta itemprop="identifier" content="{CID}">'
    assert parse_channel_page(html) == ChannelInfo(CID, "Канал & Ко")


def _items(n):
    now = datetime.now(timezone.utc)
    return [(MagicMock(video_id=f"v{i:010d}", title=f"Видео <{i}>", published_at=now - timedelta(hours=i)),
             MagicMock(title="Канал")) for i in range(n)]


def test_render_digest_pages():
    text, kb = dg.render(_items(19), 0, 123)
    assert "19 шт." in text and "стр. 1/3" in text and "&lt;0&gt;" in text
    datas = [b.callback_data for row in kb.inline_keyboard for b in row]
    assert datas.count("noop") == 2 and "dg:123:1" in datas  # ◀️ неактивна на первой странице
    assert all(len(d.encode()) <= 64 for d in datas)
    assert sum(d.startswith("s:") for d in datas) == 8
    text3, _ = dg.render(_items(19), 2, 123)
    assert "<b>17.</b>" in text3 and "<b>19.</b>" in text3


class FakeFeeds:
    def __init__(self):
        self.entries = {CID: parse_feed(FEED, CID)}
        self.calls = 0

    async def resolve(self, url):
        return ChannelInfo(CID, "Канал Тест")

    async def uploads(self, cid):
        self.calls += 1
        return self.entries.get(cid, [])


@pytest.fixture
async def ctx():
    engine = make_engine("sqlite+aiosqlite:///:memory:")
    event.listen(engine.sync_engine, "connect", lambda c, _: c.execute("PRAGMA foreign_keys=ON"))
    await init_db(engine)
    sm = make_sessionmaker(engine)
    async with sm() as s:
        await repo.upsert_user(s, MagicMock(id=1, username="u", first_name="U", language_code="ru"))
        await s.commit()
    bot = AsyncMock()
    yield {"bot": bot, "sessionmaker": sm, "settings": Settings(bot_token="1:x"), "feeds": FakeFeeds()}
    await engine.dispose()


def _rich(bot):
    """Блоки последнего rich-сообщения и клавиатура."""
    a = bot.send_rich_message.await_args
    return a.kwargs["rich_message"].blocks, a.kwargs["reply_markup"]


def _texts(blocks):
    import json
    return json.dumps([b.model_dump(mode="json", exclude_none=True, fallback=lambda _: None) for b in blocks], ensure_ascii=False)


async def test_follow_then_digest_only_new(ctx):
    await st.follow_channel(ctx, user_id=1, chat_id=1, channel_url="https://www.youtube.com/@x", status_message_id=5)
    assert "Слежу за каналом «Канал Тест»" in ctx["bot"].edit_message_text.await_args.args[0]
    blocks, kb = _rich(ctx["bot"])                   # отдельным постом — последние видео с обложками
    assert [b.type for b in blocks].count("photo") == 2 and "Второе" in _texts(blocks)
    assert "/sum_" in _texts(blocks) and "/dl_" in _texts(blocks)

    # курсор сводки — момент первой настройки: старые видео в сводку не попадут
    await st.make_digest(ctx, user_id=1, chat_id=1, manual=True, status_message_id=6)
    assert "Новых видео нет" in ctx["bot"].edit_message_text.await_args.args[0]

    # вышло новое видео
    ctx["feeds"].entries[CID].append(FeedEntry("vid00000003", CID, "Свежак", datetime.now(timezone.utc)))
    async with ctx["sessionmaker"]() as s:
        for ch in await sr.user_channels(s, 1):
            ch.checked_at = None  # чтобы сводка перепроверила канал
        await s.commit()
    await st.make_digest(ctx, user_id=1, chat_id=1, manual=True, status_message_id=7)
    blocks, kb = _rich(ctx["bot"])
    t = _texts(blocks)
    assert "Свежак" in t and "1 шт." in t and "Второе" not in t
    assert kb.inline_keyboard[0][0].callback_data == "s:vid00000003"

    # повторно — пусто
    await st.make_digest(ctx, user_id=1, chat_id=1, manual=True, status_message_id=8)
    assert "Новых видео нет" in ctx["bot"].edit_message_text.await_args.args[0]


async def test_scheduled_digest_respects_hour(ctx):
    async with ctx["sessionmaker"]() as s:
        await sr.follow(s, 1, CID, "Канал Тест")
        ds = await sr.get_digest_settings(s, 1, "Europe/Moscow", 9)
        ds.cursor = datetime(2026, 1, 1, tzinfo=timezone.utc)
        ds.hour = (datetime.now(ZoneInfo("Europe/Moscow")).hour + 1) % 24  # не сейчас
        await s.commit()
    await st.send_due_digests(ctx)
    ctx["bot"].send_rich_message.assert_not_awaited()

    async with ctx["sessionmaker"]() as s:
        (await s.get(DigestSettings, 1)).hour = datetime.now(ZoneInfo("Europe/Moscow")).hour
        await s.commit()
    await st.send_due_digests(ctx)
    assert "2 шт." in _texts(_rich(ctx["bot"])[0])
    await st.send_due_digests(ctx)  # второй раз в тот же день — не шлём
    assert ctx["bot"].send_rich_message.await_count == 1


class FakeGoogle:
    def __init__(self, subs):
        self.subs, self.polls = subs, 0

    async def start_device_flow(self):
        return {"device_code": "dc", "user_code": "ABCD-EFGH", "verification_url": "https://www.google.com/device",
                "expires_in": 60, "interval": 0}

    async def poll_token(self, code):
        self.polls += 1
        if self.polls == 1:
            raise Pending()
        return {"access_token": "at", "refresh_token": "rt"}

    async def refresh(self, rt):
        return "at2"

    async def subscriptions(self, at):
        return self.subs


async def test_import_and_sync_youtube(ctx):
    ctx["settings"] = Settings(bot_token="1:x", google_client_id="id", google_client_secret="sec")
    other = "UC" + "b" * 22
    ctx["google"] = FakeGoogle([(CID, "Канал Тест"), (other, "Другой")])
    await st.import_youtube(ctx, user_id=1, chat_id=1, status_message_id=5)
    texts = [c.args[0] for c in ctx["bot"].edit_message_text.await_args_list]
    assert "ABCD-EFGH" in texts[0] and "новых в боте: 2" in texts[-1]
    async with ctx["sessionmaker"]() as s:
        assert (await s.get(GoogleAccount, 1)).refresh_token == "rt"
        await sr.follow(s, 1, "UC" + "c" * 22, "Ручной")  # добавлен вручную — синхронизация не трогает
        await s.commit()

    ctx["google"].subs = [(CID, "Канал Тест")]  # отписался на YouTube от «Другой»
    await st.sync_youtube_subs(ctx)
    async with ctx["sessionmaker"]() as s:
        ids = set((await s.execute(select(UserChannel.channel_id).where(UserChannel.user_id == 1))).scalars())
    assert ids == {CID, "UC" + "c" * 22}


async def test_import_without_keys(ctx):
    await st.import_youtube(ctx, user_id=1, chat_id=1, status_message_id=5)
    assert "не настроен" in ctx["bot"].edit_message_text.await_args.args[0]
