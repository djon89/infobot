import json
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from app import posts as pst
from app.bot import handlers
from app.config import Settings
from app.db.models import Post
from app.db.session import init_db, make_engine, make_sessionmaker
from app.services.feeds import ChannelInfo, FeedEntry
from app.worker import subs_tasks as st

CID = "UC" + "a" * 22


def _post(kind, n, pid=7, **meta):
    now = datetime.now(timezone.utc)
    items = [pst.item(f"v{i:010d}", f"Видео {i}", "Канал", now - timedelta(hours=i), 600 + i, f"суть {i}")
             for i in range(n)]
    return Post(id=pid, user_id=1, kind=kind, items=items, meta=meta)


def _cbs(kb):
    return [b.callback_data for row in kb.inline_keyboard for b in row]


def test_digest_post_pages_and_commands():
    p = _post("digest", 14)
    b0 = pst.blocks(p, 0)
    assert [x.type for x in b0].count("photo") == pst.DIGEST_PAGE
    t = json.dumps([x.model_dump(mode="json", exclude_none=True, fallback=lambda _: None) for x in b0], ensure_ascii=False)
    assert "/sum_7_1" in t and "/dl_7_6" in t and "/sum_7_7" not in t and "суть 0" in t and "стр. 1/3" in t
    assert "/sum_7_13" in pst.as_html(p, 2)
    cbs = _cbs(pst.keyboard(p, 1))
    assert "pg:7:0" in cbs and "pg:7:2" in cbs and all(len(c.encode()) <= 64 for c in cbs)


def test_channel_post_keyboard():
    p = _post("channel", 5, channel_id=CID, channel_title="Канал", offset=5, has_more=True, followed=False)
    cbs = _cbs(pst.keyboard(p))
    assert f"chb:{CID}:0:7" in cbs and f"chb:{CID}:10:7" in cbs and f"fc:{CID}:7" in cbs
    assert all(len(c.encode()) <= 64 for c in cbs)
    p.meta = {**p.meta, "followed": True, "has_more": False, "offset": 0}
    assert not any(c.startswith(("fc:", "chb:")) for c in _cbs(pst.keyboard(p)))


def test_parse_command():
    assert pst.parse_command("/sum_12_3") == ("sum", 12, 3)
    assert pst.parse_command("/dl_5_1@my_bot") == ("dl", 5, 1)
    assert pst.parse_command("/sum_x_1") is None


@pytest.fixture
async def env():
    engine = make_engine("sqlite+aiosqlite:///:memory:")
    await init_db(engine)
    sm = make_sessionmaker(engine)
    async with sm() as s:
        from app.db import repo
        for uid in (1, 2):
            await repo.upsert_user(s, MagicMock(id=uid, username="u", first_name="U", language_code="ru"))
        s.add(_post("digest", 3, pid=7))
        await s.commit()
    yield {"settings": Settings(bot_token="1:x", owner_ids=[1]), "sessionmaker": sm, "arq": AsyncMock()}
    await engine.dispose()


def msg(text, uid=1):
    m = MagicMock(text=text)
    m.from_user = MagicMock(id=uid, username="u", first_name="U", language_code="ru")
    m.chat.id = 10
    m.answer = AsyncMock(return_value=MagicMock(message_id=99))
    return m


async def test_post_commands(env):
    await handlers.post_command(msg("/sum_7_2"), env["settings"], env["sessionmaker"], env["arq"])
    a = env["arq"].enqueue_job.await_args
    assert a.args[0] == "process_youtube" and a.kwargs["video_id"] == "v0000000001"
    await handlers.post_command(msg("/dl_7_3"), env["settings"], env["sessionmaker"], env["arq"])
    a = env["arq"].enqueue_job.await_args
    assert a.args[0] == "download_video" and a.kwargs["video_id"] == "v0000000002" and a.kwargs["quality"] == "720"
    for bad in (msg("/sum_7_9"), msg("/sum_7_1", uid=2), msg("/sum_99_1")):  # нет номера / чужой / нет поста
        await handlers.post_command(bad, env["settings"], env["sessionmaker"], env["arq"])
        assert "Не нашёл" in bad.answer.await_args.args[0]
    assert env["arq"].enqueue_job.await_count == 2


class FakeFeeds:
    async def uploads(self, cid):
        now = datetime.now(timezone.utc)
        return [FeedEntry(f"r{i:010d}", cid, f"RSS {i}", now - timedelta(days=i)) for i in range(15)]

    async def resolve(self, url):
        return ChannelInfo(CID, "Канал", "Описание канала про науку")


async def test_channel_pages_rss_then_ytdlp(env):
    dl = MagicMock()
    dl.uploads = AsyncMock(return_value=[{"id": f"y{i:010d}", "title": f"Старое {i}", "duration": 100} for i in range(5)])
    ctx = {"feeds": FakeFeeds(), "downloader": dl, "sessionmaker": env["sessionmaker"]}
    items, more = await st.channel_page(ctx, CID, 10)
    assert [i["title"] for i in items] == [f"RSS {i}" for i in range(10, 15)] and more
    dl.uploads.assert_not_awaited()
    items, more = await st.channel_page(ctx, CID, 15)   # дальше RSS — через yt-dlp
    assert items[0]["title"] == "Старое 0" and items[0]["duration"] == 100 and more
    dl.uploads.assert_awaited_once_with(CID, 15, 5)


async def test_follow_shows_description_and_post(env):
    bot = AsyncMock()
    ctx = {"feeds": FakeFeeds(), "downloader": MagicMock(), "sessionmaker": env["sessionmaker"], "bot": bot,
           "settings": env["settings"]}
    await st.follow_channel(ctx, user_id=1, chat_id=1, channel_url="https://www.youtube.com/@x", status_message_id=5)
    blocks = bot.send_rich_message.await_args.kwargs["rich_message"].blocks
    t = json.dumps([x.model_dump(mode="json", exclude_none=True, fallback=lambda _: None) for x in blocks], ensure_ascii=False)
    assert "Описание канала про науку" in t and "Видео 1–5" in t and "✅ вы следите" in t
