import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.bot import handlers
from app.config import Settings
from app.db.session import init_db, make_engine, make_sessionmaker
from app.services.downloader import summarize_formats
from tests.test_download import INFO


@pytest.fixture
async def env():
    engine = make_engine("sqlite+aiosqlite:///:memory:")
    await init_db(engine)
    yield {"settings": Settings(bot_token="1:x", owner_ids=[1]), "sessionmaker": make_sessionmaker(engine),
           "arq": AsyncMock()}
    await engine.dispose()


def cb(data, uid=1):
    c = MagicMock()
    c.data = data
    c.from_user = MagicMock(id=uid, username="u", first_name="U", language_code="ru")
    c.message.chat.id = 10
    c.message.answer = AsyncMock(return_value=MagicMock(message_id=77))
    c.message.edit_text = AsyncMock()
    c.answer = AsyncMock()
    return c


async def test_settings_then_default_download(env):
    await handlers.settings_cb(cb("set:q:1080"), env["settings"], env["sessionmaker"])
    await handlers.settings_cb(cb("set:a:ru"), env["settings"], env["sessionmaker"])
    await handlers.download_cb(cb("dl:dQw4w9WgXcQ:d:d"), env["settings"], env["sessionmaker"], env["arq"])
    kw = env["arq"].enqueue_job.await_args.kwargs
    assert env["arq"].enqueue_job.await_args.args[0] == "download_video"
    assert (kw["quality"], kw["audio"], kw["status_message_id"]) == ("1080", "ru", 77)


async def test_bad_setting_ignored(env):
    c = cb("set:q:9999")
    await handlers.settings_cb(c, env["settings"], env["sessionmaker"])
    c.message.edit_text.assert_not_awaited()


async def test_quality_with_several_tracks_asks_audio(env):
    from dataclasses import asdict
    env["arq"].get.return_value = json.dumps(asdict(summarize_formats(INFO)))
    c = cb("q:dQw4w9WgXcQ:720")
    big = Settings(bot_token="1:x", owner_ids=[1], telegram_api_url="http://telegram-bot-api:8081")
    await handlers.quality_cb(c, big, env["sessionmaker"], env["arq"])
    env["arq"].enqueue_job.assert_not_awaited()
    btns = [b.callback_data for row in c.message.edit_text.await_args.kwargs["reply_markup"].inline_keyboard for b in row]
    assert btns == ["dl:dQw4w9WgXcQ:720:en", "dl:dQw4w9WgXcQ:720:ru"]


async def test_quality_too_big_refused(env):
    from dataclasses import asdict
    env["arq"].get.return_value = json.dumps(asdict(summarize_formats(INFO)))
    c = cb("q:dQw4w9WgXcQ:best")  # 910 МБ > 50 МБ без своего Bot API
    await handlers.quality_cb(c, env["settings"], env["sessionmaker"], env["arq"])
    assert c.answer.await_args.kwargs.get("show_alert")
    env["arq"].enqueue_job.assert_not_awaited()


async def test_digest_hour_setting(env):
    c = cb("set:h:21")
    await handlers.settings_cb(c, env["settings"], env["sessionmaker"])
    assert "в <b>21:00</b>" in c.message.edit_text.await_args.args[0]
    c = cb("set:h:off")
    await handlers.settings_cb(c, env["settings"], env["sessionmaker"])
    assert "выключена" in c.message.edit_text.await_args.args[0]
    c = cb("set:h:25")
    await handlers.settings_cb(c, env["settings"], env["sessionmaker"])
    c.message.edit_text.assert_not_awaited()


async def test_short_and_sections_and_stats(env):
    from app.db.models import Video
    from tests.fixtures_summary import SUMMARY
    async with env["sessionmaker"]() as s:
        s.add(Video(id="dQw4w9WgXcQ", title="Тест", summary=json.dumps(SUMMARY, ensure_ascii=False)))
        await s.commit()
    c = cb("sh:dQw4w9WgXcQ")
    c.bot = AsyncMock()
    await handlers.short_cb(c, env["sessionmaker"])
    blocks = c.bot.send_rich_message.await_args.kwargs["rich_message"].blocks
    assert blocks[0].text.startswith("⚡") and len(blocks[3].items) == 3   # только 3 тезиса

    c = cb("dp:dQw4w9WgXcQ")
    await handlers.sections_cb(c, env["sessionmaker"])
    kbd = c.message.answer.await_args.kwargs["reply_markup"].inline_keyboard
    assert [r[0].callback_data for r in kbd] == ["dd:dQw4w9WgXcQ:0", "dd:dQw4w9WgXcQ:1"]

    await handlers.deep_cb(cb("dd:dQw4w9WgXcQ:1"), env["settings"], env["sessionmaker"], env["arq"])
    assert env["arq"].enqueue_job.await_args.kwargs["idx"] == 1

    m = MagicMock(from_user=MagicMock(id=1), answer=AsyncMock())
    await handlers.stats_cmd(m, env["settings"], env["sessionmaker"])
    assert "Статистика бота" in m.answer.await_args.args[0]
    m2 = MagicMock(from_user=MagicMock(id=2), answer=AsyncMock())
    await handlers.stats_cmd(m2, env["settings"], env["sessionmaker"])
    assert "только владельцу" in m2.answer.await_args.args[0]
