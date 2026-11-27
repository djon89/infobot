from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.telegram import TelegramAPIServer
from aiogram.enums import ParseMode

from app.config import Settings


def make_bot(s: Settings) -> Bot:
    session = None
    if s.telegram_api_url:
        session = AiohttpSession(api=TelegramAPIServer.from_base(s.telegram_api_url, is_local=True))
    return Bot(s.bot_token, session=session, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
