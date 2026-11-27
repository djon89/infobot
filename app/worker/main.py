import logging

from arq import func
from arq.connections import RedisSettings

from app.config import get_settings
from app.db.session import init_db, make_engine, make_sessionmaker
from app.services.downloader import Downloader
from app.services.llm.factory import LLMRouter
from app.services.transcript.chain import TranscriptChain
from app.services.transcript.yta import YTATranscriptProvider

from app.telegram_client import make_bot

from .tasks import download_video, probe_video, process_youtube

settings = get_settings()


async def startup(ctx):
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    engine = make_engine(settings.database_url)
    await init_db(engine)
    ctx["engine"] = engine
    ctx["sessionmaker"] = make_sessionmaker(engine)
    ctx["settings"] = settings
    ctx["bot"] = make_bot(settings)
    ctx["downloader"] = Downloader(
        settings.downloads_dir, settings.max_upload_bytes, settings.bgutil_url,
        settings.ytdlp_proxy or settings.generic_proxy_url or None,
    )
    ctx["downloader"].cleanup(0)  # остатки после перезапуска
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
    functions = [process_youtube, func(probe_video, timeout=120), func(download_video, timeout=3600)]
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = RedisSettings.from_dsn(settings.redis_url)
    max_jobs = 4
    job_timeout = 900
    max_tries = 1
