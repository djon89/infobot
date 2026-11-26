import logging

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from arq.connections import RedisSettings

from app.config import get_settings
from app.db.session import init_db, make_engine, make_sessionmaker
from app.services.llm.factory import LLMRouter
from app.services.transcript.chain import TranscriptChain
from app.services.transcript.yta import YTATranscriptProvider

from .tasks import process_youtube

settings = get_settings()


async def startup(ctx):
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    engine = make_engine(settings.database_url)
    await init_db(engine)
    ctx["engine"] = engine
    ctx["sessionmaker"] = make_sessionmaker(engine)
    ctx["settings"] = settings
    ctx["bot"] = Bot(settings.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    ctx["llm"] = LLMRouter(settings)
    ctx["transcripts"] = TranscriptChain([
        YTATranscriptProvider(
            settings.transcript_languages,
            settings.webshare_proxy_username, settings.webshare_proxy_password,
            settings.generic_proxy_url,
        ),
        # сюда добавятся: yt-dlp subtitles, Whisper по аудио (для видео без субтитров)
    ])


async def shutdown(ctx):
    await ctx["bot"].session.close()
    await ctx["engine"].dispose()


class WorkerSettings:
    functions = [process_youtube]
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = RedisSettings.from_dsn(settings.redis_url)
    max_jobs = 4
    job_timeout = 900
    max_tries = 1
