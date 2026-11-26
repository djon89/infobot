import logging

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import BufferedInputFile

from app.db import repo
from app.db.models import Video
from app.services.llm.base import LLMError
from app.services.summarizer import summarize
from app.services.transcript.base import TranscriptError
from app.services.youtube import VideoMeta, fetch_meta
from app.telegram_format import md_to_tg_html, safe_filename, split_message

log = logging.getLogger(__name__)


async def _status(bot: Bot, chat_id: int, msg_id: int, text: str):
    try:
        await bot.edit_message_text(text, chat_id=chat_id, message_id=msg_id)
    except TelegramBadRequest:
        pass


async def process_youtube(ctx, *, job_id: int, user_id: int, chat_id: int, video_id: str,
                          status_message_id: int, reply_to: int | None = None):
    bot: Bot = ctx["bot"]
    sm = ctx["sessionmaker"]
    s = ctx["settings"]
    llm = ctx["llm"].for_user(user_id)

    async def fail(text: str, err: str):
        await _status(bot, chat_id, status_message_id, f"❌ {text}")
        async with sm() as db:
            await repo.finish_job(db, job_id, False, llm.name, err)
            await db.commit()

    try:
        # 1. Расшифровка (из кэша или с YouTube)
        await _status(bot, chat_id, status_message_id, "⏳ Получаю расшифровку…")
        async with sm() as db:
            video = await db.get(Video, video_id)
            transcript = repo.video_transcript(video)
            meta = VideoMeta(video_id, video.title, video.author) if video else None
        if transcript is None:
            meta = await fetch_meta(video_id, s.generic_proxy_url or None)
            transcript = await ctx["transcripts"].get(video_id)
            async with sm() as db:
                await repo.save_transcript(db, meta, transcript)
                await db.commit()

        kind = "авто-субтитры" if transcript.is_generated else "субтитры автора"
        header = f"{meta.title}\n{meta.url}\nЯзык: {transcript.language} ({kind})\n\n"
        await bot.send_document(
            chat_id,
            BufferedInputFile((header + transcript.as_timestamped()).encode(),
                              filename=safe_filename(meta.title, "расшифровка.txt")),
            caption="📄 Расшифровка",
            reply_to_message_id=reply_to,
        )

        # 2. Конспект (из кэша или от модели)
        await _status(bot, chat_id, status_message_id, "✍️ Пишу конспект…")
        async with sm() as db:
            video = await db.get(Video, video_id)
            summary = video.summary if video else None
        if not summary:
            summary = await summarize(llm, meta, transcript)
            async with sm() as db:
                video = await db.get(Video, video_id)
                video.summary = summary
                await db.commit()

        title_html = f'<b>📝 <a href="{meta.url}">{_esc(meta.title)}</a></b>\n\n'
        for i, part in enumerate(split_message(title_html + md_to_tg_html(summary))):
            await bot.send_message(chat_id, part, disable_web_page_preview=True,
                                   reply_to_message_id=reply_to if i == 0 else None)
        await bot.send_document(
            chat_id,
            BufferedInputFile(f"# {meta.title}\n{meta.url}\n\n{summary}\n".encode(),
                              filename=safe_filename(meta.title, "конспект.md")),
        )
        try:
            await bot.delete_message(chat_id, status_message_id)
        except TelegramBadRequest:
            pass
        async with sm() as db:
            await repo.finish_job(db, job_id, True, llm.name)
            await db.commit()
    except TranscriptError as e:
        await fail(str(e), repr(e))
    except LLMError as e:
        await fail(f"Расшифровку отправил, а конспект не получился: {e}", repr(e))
    except Exception as e:  # noqa: BLE001
        log.exception("job %s failed", job_id)
        await fail("Что-то пошло не так. Попробуйте ещё раз позже.", repr(e))


def _esc(t: str) -> str:
    import html
    return html.escape(t, quote=True)
