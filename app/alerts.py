"""Ошибки — владельцу в Telegram (не чаще раза в 10 минут на одну и ту же ошибку)."""
import asyncio
import html
import logging
import time

from aiogram import Bot

THROTTLE_S = 600


class TelegramAlertHandler(logging.Handler):
    def __init__(self, bot: Bot, owner_ids: list[int], source: str):
        super().__init__(level=logging.ERROR)
        self.bot, self.owner_ids, self.source = bot, owner_ids, source
        self._last: dict[str, float] = {}

    def emit(self, record: logging.LogRecord) -> None:
        if record.name.startswith(("aiogram", "app.alerts")) or not self.owner_ids:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return  # вне event loop (например, из потока yt-dlp) — пропускаем
        key = f"{record.name}:{record.getMessage()[:120]}"
        now = time.monotonic()
        if now - self._last.get(key, -THROTTLE_S) < THROTTLE_S:
            return
        self._last[key] = now
        exc = ""
        if record.exc_info and record.exc_info[1] is not None:
            exc = f"\n<code>{html.escape(repr(record.exc_info[1])[:600])}</code>"
        text = f"🚨 <b>Ошибка</b> ({self.source})\n{html.escape(record.getMessage()[:500])}{exc}"
        for uid in self.owner_ids:
            loop.create_task(self._send(uid, text))

    async def _send(self, uid: int, text: str):
        try:
            await self.bot.send_message(uid, text)
        except Exception:  # noqa: BLE001
            pass


def install(bot: Bot, owner_ids: list[int], source: str) -> None:
    logging.getLogger().addHandler(TelegramAlertHandler(bot, owner_ids, source))
