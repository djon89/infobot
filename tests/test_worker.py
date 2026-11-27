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

    async def complete_json(self, system, prompt, schema):
        import copy
        from tests.fixtures_summary import DEEP, SUMMARY
        self.calls += 1
        assert "<transcript>" in prompt
        return copy.deepcopy(DEEP if "Разбери подробно" in prompt else SUMMARY)


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
        "dQw4w9WgXcQ", "en", True, [Segment(0, "hello"), Segment(61, "world"), Segment(700, "bye")], "fake"))
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
    rich = bot.send_rich_message.await_args
    blocks = rich.kwargs["rich_message"].blocks
    assert blocks[0].type == "photo" and blocks[0].photo.media.endswith("/dQw4w9WgXcQ/maxresdefault.jpg")
    assert blocks[1].type == "heading" and blocks[1].text.startswith("Сон важнее")
    assert any(b.type == "table" for b in blocks)
    cbs = [b.callback_data for row in rich.kwargs["reply_markup"].inline_keyboard for b in row]
    assert cbs[:2] == ["sh:dQw4w9WgXcQ", "dp:dQw4w9WgXcQ"]
    md = bot.send_document.await_args_list[1].args[1].data.decode()
    assert "## 🔑 Главное" in md and "(https://youtu.be/dQw4w9WgXcQ?t=75)" in md

    # второй запрос того же видео — из кэша, без YouTube и без LLM
    await tasks.process_youtube(ctx, job_id=2, **kw)
    assert ctx["transcripts"].get.await_count == 1
    assert ctx["_llm"].calls == 1
    async with ctx["sessionmaker"]() as s:
        assert (await s.get(Job, 2)).status == "done"
        assert '"headline"' in (await s.get(Video, "dQw4w9WgXcQ")).summary
        assert await repo.jobs_last_24h(s, 1) == 2


async def test_no_transcript(ctx):
    ctx["transcripts"].get = AsyncMock(side_effect=NoTranscript("У этого видео нет субтитров."))
    await tasks.process_youtube(ctx, job_id=1, user_id=1, chat_id=10, video_id="dQw4w9WgXcQ", status_message_id=5)
    last = ctx["bot"].edit_message_text.await_args
    assert last.args[0].startswith("❌ У этого видео нет субтитров")
    async with ctx["sessionmaker"]() as s:
        assert (await s.get(Job, 1)).status == "failed"


async def test_no_api_key_still_sends_transcript(ctx):
    from app.services.llm.factory import LLMRouter
    ctx["llm"] = LLMRouter(Settings(bot_token="1:x", owner_ids=[999], anthropic_api_key=None))
    await tasks.process_youtube(ctx, job_id=1, user_id=1, chat_id=10, video_id="dQw4w9WgXcQ", status_message_id=5)
    assert ctx["bot"].send_document.await_count == 1  # расшифровка ушла
    assert "не настроены" in ctx["bot"].edit_message_text.await_args.args[0]



async def test_rich_rejected_falls_back_to_html(ctx):
    from aiogram.exceptions import TelegramBadRequest
    ctx["bot"].send_rich_message.side_effect = TelegramBadRequest(method=MagicMock(), message="RICH_UNSUPPORTED")
    await tasks.process_youtube(ctx, job_id=1, user_id=1, chat_id=10, video_id="dQw4w9WgXcQ", status_message_id=5)
    text = ctx["bot"].send_message.await_args.args[1]
    assert "<b>🔑 Главное</b>" in text and 'href="https://youtu.be/dQw4w9WgXcQ?t=75"' in text


async def test_deep_dive_cached(ctx):
    await tasks.process_youtube(ctx, job_id=1, user_id=1, chat_id=10, video_id="dQw4w9WgXcQ", status_message_id=5)
    for job in (1, 2):
        await tasks.deep_dive(ctx, job_id=job, user_id=1, chat_id=10, video_id="dQw4w9WgXcQ", idx=1,
                              status_message_id=6)
    assert ctx["_llm"].calls == 2  # конспект + один разбор (второй раз из кэша)
    blocks = ctx["bot"].send_rich_message.await_args.kwargs["rich_message"].blocks
    assert blocks[0].text == "🔍 Часть 2. Кофеин и алкоголь"


async def test_legacy_text_summary_regenerated(ctx):
    async with ctx["sessionmaker"]() as s:
        s.add(Video(id="dQw4w9WgXcQ", title="Тест", segments=[[0, "hello"]], language="en",
                    is_generated=True, summary="**Старый** конспект"))
        await s.commit()
    await tasks.process_youtube(ctx, job_id=1, user_id=1, chat_id=10, video_id="dQw4w9WgXcQ", status_message_id=5)
    assert ctx["_llm"].calls == 1



async def test_cover_rejected_retries_without_it(ctx):
    from aiogram.exceptions import TelegramBadRequest
    calls = []

    async def fake_rich(chat_id, rich_message, **kw):
        calls.append([b.type for b in rich_message.blocks])
        if "photo" in calls[-1]:
            raise TelegramBadRequest(method=MagicMock(), message="WEBPAGE_MEDIA_EMPTY")
        return MagicMock()
    ctx["bot"].send_rich_message.side_effect = fake_rich
    await tasks.process_youtube(ctx, job_id=1, user_id=1, chat_id=10, video_id="dQw4w9WgXcQ", status_message_id=5)
    assert calls[0][0] == "photo" and "photo" not in calls[1]
    ctx["bot"].send_message.assert_not_awaited()  # до HTML-запасного дело не дошло
