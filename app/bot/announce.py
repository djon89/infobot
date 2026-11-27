import logging

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError

from app.changelog import VERSION, announcement, changes_since
from app.db.models import BotMeta

log = logging.getLogger(__name__)
KEY = "announced_version"


async def announce_update(bot: Bot, sessionmaker, owner_ids: list[int]) -> bool:
    """Если версия новая — написать владельцам, что изменилось. True, если сообщение ушло."""
    async with sessionmaker() as s:
        row = await s.get(BotMeta, KEY)
        last = row.value if row else None
        if last == VERSION:
            return False
        entries = changes_since(last)
        sent = False
        if entries:
            text = announcement(entries)
            for uid in owner_ids:
                try:
                    await bot.send_message(uid, text)
                    sent = True
                except TelegramAPIError as e:  # владелец ещё не нажал «Старт» и т.п.
                    log.warning("update announcement to %s failed: %s", uid, e)
        if row:
            row.value = VERSION
        else:
            s.add(BotMeta(key=KEY, value=VERSION))
        await s.commit()
    log.info("bot version %s (previous %s)", VERSION, last)
    return sent
