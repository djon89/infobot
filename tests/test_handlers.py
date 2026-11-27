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
