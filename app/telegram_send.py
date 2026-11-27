"""Отправка «статей»: rich-сообщение Telegram, а если клиент/сервер его не принял — обычный HTML."""
import logging

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import InputRichMessage, ReplyParameters

from app.telegram_format import split_message

log = logging.getLogger(__name__)


async def send_article(bot: Bot, chat_id: int, blocks: list, fallback_html: str,
                       reply_markup=None, reply_to: int | None = None):
    reply = ReplyParameters(message_id=reply_to, allow_sending_without_reply=True) if reply_to else None
    attempts = [blocks]
    if any(getattr(b, "type", None) == "photo" for b in blocks):
        attempts.append([b for b in blocks if getattr(b, "type", None) != "photo"])  # без обложки
    for attempt in attempts:
        try:
            return await bot.send_rich_message(chat_id, rich_message=InputRichMessage(blocks=attempt),
                                               reply_markup=reply_markup, reply_parameters=reply)
        except TelegramBadRequest as e:
            log.warning("rich message rejected (%s)", e)
    log.warning("sending HTML fallback")
    parts = split_message(fallback_html)
    msg = None
    for i, part in enumerate(parts):
        msg = await bot.send_message(chat_id, part, disable_web_page_preview=True,
                                     reply_parameters=reply if i == 0 else None,
                                     reply_markup=reply_markup if i == len(parts) - 1 else None)
    return msg
