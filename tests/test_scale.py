import asyncio
import logging
from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import event

from app import alerts, digest as dg, limits
from app.config import Settings
from app.db import repo
from app.db import subs_repo as sr
from app.db.models import Channel, Job, utcnow
from app.db.session import init_db, make_engine, make_sessionmaker
from app.services.blurbs import build_prompt, make_blurbs
from app.worker import subs_tasks as st


@pytest.fixture
async def sm():
    engine = make_engine("sqlite+aiosqlite:///:memory:")
    event.listen(engine.sync_engine, "connect", lambda c, _: c.execute("PRAGMA foreign_keys=ON"))
    await init_db(engine)
    sm = make_sessionmaker(engine)
    async with sm() as s:
        for uid in (1, 2):
            await repo.upsert_user(s, MagicMock(id=uid, username="u", first_name="U", language_code="ru"))
        await s.commit()
    yield sm
    await engine.dispose()


async def test_monthly_limits(sm):
    st_ = Settings(bot_token="1:x", owner_ids=[1], monthly_summaries=3, monthly_downloads=1)
    async with sm() as s:
        for kind in ("youtube_summary", "deep_dive", "youtube_summary"):
            await repo.create_job(s, 2, "x", kind=kind)
        failed = await repo.create_job(s, 2, "x")
        failed.status = "failed"                       # неудачные не считаются
        old = await repo.create_job(s, 2, "x")
        old.created_at = utcnow() - timedelta(days=31)  # вне окна 30 дней
        await s.commit()
        err = await limits.check(s, st_, 2, "summary")
        assert err and "3 конспектов и разборов" in err and "освободится" in err
        assert await limits.check(s, st_, 2, "download") is None
        assert await limits.check(s, st_, 1, "summary", 100) is None  # владелец без лимитов
        assert "конспектов и разборов 3/3" in await limits.usage_text(s, st_, 2)


class FakeLLM:
    name = "fake"

    def __init__(self):
        self.calls = []

    async def complete_json(self, system, prompt, schema):
        self.calls.append(prompt)
        import re
        return {"items": [{"id": i, "line": f"суть {i}"} for i in re.findall(r'id="([^"]+)"', prompt)]
                + [{"id": "чужой", "line": "лишнее"}]}


async def test_blurbs_cached_and_shown(sm):
    cid = "UC" + "a" * 22
    from app.services.feeds import FeedEntry
    async with sm() as s:
        await sr.follow(s, 1, cid, "Канал")
        await sr.save_entries(s, [FeedEntry("vid00000001", cid, "Видео", utcnow(), "Описание про сон")])
        await s.commit()
        items = await sr.latest_for_channel(s, cid)
    llm = FakeLLM()
    ctx = {"sessionmaker": sm, "llm": MagicMock(for_user=lambda uid: llm)}
    lines = await st.fill_blurbs(ctx, 1, items)
    assert lines == {"vid00000001": "суть vid00000001"} and "Описание про сон" in llm.calls[0]
    await st.fill_blurbs(ctx, 2, items)               # второй пользователь — из кэша
    assert len(llm.calls) == 1
    text, _ = dg.render(items, 0, 1, blurbs=lines)
    assert "↳ суть vid00000001" in text


async def test_blurbs_failure_is_silent(sm):
    cid = "UC" + "a" * 22
    from app.services.feeds import FeedEntry
    async with sm() as s:
        await sr.follow(s, 1, cid, "Канал")
        await sr.save_entries(s, [FeedEntry("vid00000001", cid, "Видео", utcnow())])
        await s.commit()
        items = await sr.latest_for_channel(s, cid)
    bad = MagicMock(for_user=MagicMock(side_effect=RuntimeError("нет ключа")))
    assert await st.fill_blurbs({"sessionmaker": sm, "llm": bad}, 1, items) == {}


def test_blurb_prompt_trims_description():
    p = build_prompt([{"id": "a", "title": "T", "channel": "C", "description": "x" * 5000}])
    assert len(p) < 800


async def test_poll_feeds_only_stale_in_batches(sm, monkeypatch):
    async with sm() as s:
        for i in range(5):
            await sr.follow(s, 1, f"UC{i:022d}", f"К{i}")
        await s.commit()
        for i, ch in enumerate(await sr.user_channels(s, 1)):
            ch.checked_at = utcnow() if i < 2 else utcnow() - timedelta(hours=2)
        await s.commit()
    monkeypatch.setattr(st, "POLL_BATCH", 2)
    polled = []

    async def fake_poll(ctx, ids):
        polled.append(ids)
        return 0
    monkeypatch.setattr(st, "poll_channels", fake_poll)
    await st.poll_feeds({"sessionmaker": sm})
    assert len(polled[0]) == 2  # из 3 устаревших — порция 2, свежие не трогаем


async def test_alert_handler_throttles():
    bot = AsyncMock()
    h = alerts.TelegramAlertHandler(bot, [1], "тест")
    log = logging.getLogger("app.test_alerts")
    log.addHandler(h)
    try:
        for _ in range(3):
            log.error("всё сломалось")
        log.warning("не ошибка")
        await asyncio.sleep(0)
        await asyncio.sleep(0)
    finally:
        log.removeHandler(h)
    assert bot.send_message.await_count == 1
    assert "всё сломалось" in bot.send_message.await_args.args[1]
