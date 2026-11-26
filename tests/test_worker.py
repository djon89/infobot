from unittest.mock import AsyncMock, MagicMock

import pytest

from app.config import Settings
from app.db import repo
from app.db.models import Job, Video
from app.db.session import init_db, make_engine, make_sessionmaker
from app.services.transcript.base import NoTranscript, Segment, Transcript
from app.services.youtube import VideoMeta
from app.worker import tasks


class FakeLLM:
    name = "fake"

    def __init__(self):
        self.calls = 0

    async def complete(self, system, prompt):
        self.calls += 1
        assert "<transcript>" in prompt
        return "**О чём видео** — тест.\n- пункт"


@pytest.fixture
async def ctx(monkeypatch):
    from sqlalchemy import event
    engine = make_engine("sqlite+aiosqlite:///:memory:")
    event.listen(engine.sync_engine, "connect", lambda c, _: c.execute("PRAGMA foreign_keys=ON"))
    await init_db(engine)
    sm = make_sessionmaker(engine)
    async with sm() as s:
        await repo.upsert_user(s, MagicMock(id=1, username="u", first_name="U", language_code="ru"))
        await repo.create_job(s, 1, "dQw4w9WgXcQ")
        await repo.create_job(s, 1, "dQw4w9WgXcQ")
        await s.commit()
    monkeypatch.setattr(tasks, "fetch_meta", AsyncMock(return_value=VideoMeta("dQw4w9WgXcQ", "Тест", "Канал")))
    llm = FakeLLM()
    transcripts = MagicMock()
    transcripts.get = AsyncMock(return_value=Transcript(
        "dQw4w9WgXcQ", "en", True, [Segment(0, "hello"), Segment(61, "world")], "fake"))
    yield {"bot": AsyncMock(), "sessionmaker": sm, "settings": Settings(bot_token="1:x"),
           "llm": MagicMock(for_user=lambda uid: llm), "transcripts": transcripts, "_llm": llm}
    await engine.dispose()


async def test_full_flow_and_cache(ctx):
    kw = dict(user_id=1, chat_id=10, video_id="dQw4w9WgXcQ", status_message_id=5, reply_to=4)
    await tasks.process_youtube(ctx, job_id=1, **kw)
    bot = ctx["bot"]
    assert bot.send_document.await_count == 2
    names = [c.args[1].filename for c in bot.send_document.await_args_list]
    assert names == ["Тест — расшифровка.txt", "Тест — конспект.md"]
    txt = bot.send_document.await_args_list[0].args[1].data.decode()
    assert "[01:01] world" in txt
    html_msg = bot.send_message.await_args.args[1]
    assert "<b>О чём видео</b>" in html_msg and "• пункт" in html_msg

    # второй запрос того же видео — из кэша, без YouTube и без LLM
    await tasks.process_youtube(ctx, job_id=2, **kw)
    assert ctx["transcripts"].get.await_count == 1
    assert ctx["_llm"].calls == 1
    async with ctx["sessionmaker"]() as s:
        assert (await s.get(Job, 2)).status == "done"
        assert (await s.get(Video, "dQw4w9WgXcQ")).summary.startswith("**О чём")
        assert await repo.jobs_last_24h(s, 1) == 2


async def test_no_transcript(ctx):
    ctx["transcripts"].get = AsyncMock(side_effect=NoTranscript("У этого видео нет субтитров."))
    await tasks.process_youtube(ctx, job_id=1, user_id=1, chat_id=10, video_id="dQw4w9WgXcQ", status_message_id=5)
    last = ctx["bot"].edit_message_text.await_args
    assert last.args[0].startswith("❌ У этого видео нет субтитров")
    async with ctx["sessionmaker"]() as s:
        assert (await s.get(Job, 1)).status == "failed"
