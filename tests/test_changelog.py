from unittest.mock import AsyncMock

import pytest
from aiogram.exceptions import TelegramForbiddenError

from app.bot.announce import announce_update
from app.changelog import CHANGES, VERSION, _key, announcement, changes_since
from app.db.session import init_db, make_engine, make_sessionmaker


def test_changelog_is_ordered_and_current():
    keys = [_key(v) for v, _, _ in CHANGES]
    assert keys == sorted(keys, reverse=True) and len(set(keys)) == len(keys)
    assert VERSION == CHANGES[0][0] and all(items for _, _, items in CHANGES)


def test_changes_since():
    assert changes_since(None) == CHANGES[:1]
    assert [v for v, _, _ in changes_since("1.2.0")] == [v for v, _, _ in CHANGES if _key(v) > (1, 2, 0)]
    assert changes_since(VERSION) == []
    text = announcement(changes_since("1.2.0"))
    assert f"версии {VERSION}" in text and "<b>1.3.0</b>" in text


@pytest.fixture
async def sm():
    engine = make_engine("sqlite+aiosqlite:///:memory:")
    await init_db(engine)
    yield make_sessionmaker(engine)
    await engine.dispose()


async def test_announce_once_per_version(sm):
    bot = AsyncMock()
    assert await announce_update(bot, sm, [1, 2]) is True
    assert bot.send_message.await_count == 2
    assert await announce_update(bot, sm, [1, 2]) is False  # перезапуск без новой версии — тишина
    assert bot.send_message.await_count == 2


async def test_announce_survives_blocked_owner(sm):
    bot = AsyncMock()
    bot.send_message.side_effect = TelegramForbiddenError(method=AsyncMock(), message="blocked")
    assert await announce_update(bot, sm, [1]) is False
    assert await announce_update(AsyncMock(), sm, [1]) is False  # версия всё равно запомнена
