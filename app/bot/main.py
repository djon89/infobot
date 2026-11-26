import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import BotCommand
from arq import create_pool
from arq.connections import RedisSettings

from app.config import get_settings
from app.db.session import init_db, make_engine, make_sessionmaker

from .handlers import router
from .middlewares import AccessMiddleware


async def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    s = get_settings()
    engine = make_engine(s.database_url)
    await init_db(engine)
    arq = await create_pool(RedisSettings.from_dsn(s.redis_url))

    bot = Bot(s.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher(settings=s, sessionmaker=make_sessionmaker(engine), arq=arq)
    dp.message.middleware(AccessMiddleware(s))
    dp.include_router(router)
    await bot.set_my_commands([BotCommand(command="start", description="Начать"),
                               BotCommand(command="help", description="Что умеет бот")])
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
