import asyncio
import dataclasses
import json
import logging
import os
import shutil
import time

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import BufferedInputFile, FSInputFile

from app.bot import keyboards as kb
from app.db import repo
from app.db.models import DownloadLog, SectionDeepDive, Video, VideoFile
from app.services.downloader import QUALITY_LABELS, DownloadError, human_size
from app.services.llm.base import LLMError
from app import render
from app.services.summarizer import deep_dive as llm_deep_dive
from app.services.summarizer import load_summary, summarize
from app.services.transcript.base import TranscriptError
from app.services.youtube import VideoMeta, fetch_meta
from app.telegram_format import nice_name, safe_filename
from app.telegram_send import send_article

log = logging.getLogger(__name__)


async def _status(bot: Bot, chat_id: int, msg_id: int, text: str, reply_markup=None):
    try:
        await bot.edit_message_text(text, chat_id=chat_id, message_id=msg_id, reply_markup=reply_markup)
    except TelegramBadRequest:
        pass


async def _download_kb(ctx, user_id: int, video_id: str, summary: bool = False):
    s = ctx["settings"]
    async with ctx["sessionmaker"]() as db:
        us = await repo.get_user_settings(db, user_id, s.default_quality, s.default_audio)
    return (kb.summary_actions if summary else kb.video_actions)(video_id, us.quality, us.audio)


async def process_youtube(ctx, *, job_id: int, user_id: int, chat_id: int, video_id: str,
                          status_message_id: int, reply_to: int | None = None):
    bot: Bot = ctx["bot"]
    sm = ctx["sessionmaker"]
    s = ctx["settings"]
    llm_name = None

    async def fail(text: str, err: str):
        await _status(bot, chat_id, status_message_id, f"❌ {text}",
                      reply_markup=await _download_kb(ctx, user_id, video_id))
        async with sm() as db:
            await repo.finish_job(db, job_id, False, llm_name, err)
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
        await _status(bot, chat_id, status_message_id, "✍️ Пишу конспект… (1–3 минуты)")
        async with sm() as db:
            video = await db.get(Video, video_id)
            summary = load_summary(video.summary if video else None)
        if summary is None:
            llm = ctx["llm"].for_user(user_id)
            llm_name = llm.name
            summary = await summarize(llm, meta, transcript)
            async with sm() as db:
                video = await db.get(Video, video_id)
                video.summary = json.dumps(summary, ensure_ascii=False)
                await db.commit()

        await bot.send_document(
            chat_id,
            BufferedInputFile(render.summary_markdown(meta, summary).encode(),
                              filename=safe_filename(meta.title, "конспект.md")),
        )
        note = " (авто-субтитры)" if transcript.is_generated else ""
        await send_article(bot, chat_id, render.summary_blocks(meta, summary, note),
                           render.summary_html(meta, summary),
                           reply_markup=await _download_kb(ctx, user_id, video_id, summary=True),
                           reply_to=reply_to)
        try:
            await bot.delete_message(chat_id, status_message_id)
        except TelegramBadRequest:
            pass
        async with sm() as db:
            await repo.finish_job(db, job_id, True, llm_name)
            await db.commit()
    except TranscriptError as e:
        await fail(str(e), repr(e))
    except LLMError as e:
        await fail(f"Расшифровку отправил, а конспект не получился: {e}", repr(e))
    except Exception as e:  # noqa: BLE001
        log.exception("job %s failed", job_id)
        await fail("Что-то пошло не так. Попробуйте ещё раз позже.", repr(e))


async def _log_download(ctx, **kw):
    try:
        async with ctx["sessionmaker"]() as db:
            db.add(DownloadLog(**kw))
            await db.commit()
    except Exception:  # noqa: BLE001 — статистика не должна ломать отправку
        log.exception("download log failed")


async def probe_video(ctx, *, chat_id: int, video_id: str, status_message_id: int):
    bot: Bot = ctx["bot"]
    try:
        p = await ctx["downloader"].probe(video_id)
    except DownloadError as e:
        await _status(bot, chat_id, status_message_id, f"❌ {e}")
        return
    except Exception:  # noqa: BLE001
        log.exception("probe %s failed", video_id)
        await _status(bot, chat_id, status_message_id, "❌ Не удалось получить список качеств.")
        return
    await ctx["redis"].set(f"probe:{video_id}", json.dumps(dataclasses.asdict(p)), ex=3600)
    await _status(bot, chat_id, status_message_id, f"🎚 <b>{_esc(p.title)}</b>\nВыберите качество:",
                  reply_markup=kb.quality_picker(video_id, p.sizes, ctx["settings"].max_upload_bytes))


async def download_video(ctx, *, job_id: int, user_id: int, chat_id: int, video_id: str,
                         quality: str, audio: str, status_message_id: int):
    bot: Bot = ctx["bot"]
    sm = ctx["sessionmaker"]
    label = QUALITY_LABELS.get(quality, quality)

    async def finish(ok: bool, err: str | None = None):
        async with sm() as db:
            await repo.finish_job(db, job_id, ok, None, err)
            await db.commit()

    # 1. Уже отправляли такой файл — пересылаем мгновенно по file_id
    async with sm() as db:
        cached = await repo.find_video_file(db, video_id, quality, audio)
    if cached:
        try:
            send = bot.send_audio if cached.is_audio else bot.send_video
            await send(chat_id, cached.file_id)
            await bot.delete_message(chat_id, status_message_id)
            await finish(True)
            await _log_download(ctx, user_id=user_id, video_id=video_id, quality=quality,
                                bytes=cached.size or 0, cached=True)
            return
        except TelegramBadRequest:
            log.warning("cached file_id for %s is invalid, downloading again", video_id)

    # 2. Скачиваем с прогрессом
    loop = asyncio.get_running_loop()
    last = {"t": 0.0}

    def on_progress(done: int, total: int):
        now = time.monotonic()
        if now - last["t"] < 4 or not total:
            return
        last["t"] = now
        pct = min(99, int(done * 100 / total))
        text = f"⬇️ Скачиваю ({label})… {pct}% из ~{human_size(total)}"
        asyncio.run_coroutine_threadsafe(_status(bot, chat_id, status_message_id, text), loop)

    await _status(bot, chat_id, status_message_id, f"⬇️ Скачиваю ({label})…")
    path = None
    try:
        f = await ctx["downloader"].download(video_id, quality, audio, on_progress)
        # человеческое имя файла: «Название видео.mp4»
        ext = f.path.rsplit(".", 1)[-1]
        job_dir = os.path.join(os.path.dirname(f.path), f"job{job_id}")
        os.makedirs(job_dir, exist_ok=True)
        path = os.path.join(job_dir, f"{nice_name(f.title)}.{ext}")
        os.replace(f.path, path)
        await _status(bot, chat_id, status_message_id, f"⬆️ Отправляю в Telegram ({human_size(f.size)})…")
        media = (f"file://{path}" if ctx["settings"].telegram_api_url
                 else FSInputFile(path))
        caption = f"🎬 <a href=\"https://www.youtube.com/watch?v={video_id}\">{_esc(f.title)}</a>"
        if f.is_audio:
            msg = await bot.send_audio(chat_id, media, caption=caption, title=f.title,
                                       duration=f.duration or None, request_timeout=3600)
            file_id = msg.audio.file_id
        else:
            msg = await bot.send_video(chat_id, media, caption=caption, supports_streaming=True,
                                       width=f.width, height=f.height, duration=f.duration or None,
                                       request_timeout=3600)
            file_id = msg.video.file_id if msg.video else msg.document.file_id
        async with sm() as db:
            db.add(VideoFile(video_id=video_id, quality=quality, audio=audio,
                             is_audio=f.is_audio, file_id=file_id, size=f.size))
            await db.commit()
        try:
            await bot.delete_message(chat_id, status_message_id)
        except TelegramBadRequest:
            pass
        await finish(True)
        await _log_download(ctx, user_id=user_id, video_id=video_id, quality=quality, bytes=f.size,
                            via_proxy=f.via_proxy, direct_blocked=f.direct_blocked)
    except DownloadError as e:
        await _status(bot, chat_id, status_message_id, f"❌ {e}")
        await finish(False, repr(e))
        await _log_download(ctx, user_id=user_id, video_id=video_id, quality=quality, ok=False,
                            direct_blocked=getattr(e, "direct_blocked", False))
    except Exception as e:  # noqa: BLE001
        log.exception("download %s failed", video_id)
        await _status(bot, chat_id, status_message_id, "❌ Не удалось скачать или отправить видео.")
        await finish(False, repr(e))
    finally:
        if path:
            shutil.rmtree(os.path.dirname(path), ignore_errors=True)


async def deep_dive(ctx, *, job_id: int, user_id: int, chat_id: int, video_id: str, idx: int,
                    status_message_id: int):
    bot, sm = ctx["bot"], ctx["sessionmaker"]

    async def finish(ok: bool, err: str | None = None, provider: str | None = None):
        async with sm() as db:
            await repo.finish_job(db, job_id, ok, provider, err)
            await db.commit()

    async with sm() as db:
        video = await db.get(Video, video_id)
        summary = load_summary(video.summary if video else None)
        transcript = repo.video_transcript(video)
        cached = await db.get(SectionDeepDive, (video_id, idx))
    if not summary or not transcript or idx >= len(summary["sections"]):
        await _status(bot, chat_id, status_message_id, "❌ Конспект устарел — пришлите ссылку на видео ещё раз.")
        await finish(False, "no summary")
        return
    meta = VideoMeta(video_id, video.title, video.author)
    sec = summary["sections"][idx]
    provider = None
    try:
        if cached:
            deep = cached.data
        else:
            await _status(bot, chat_id, status_message_id, f"🔍 Разбираю подробно: «{_esc(sec['title'])}»…")
            llm = ctx["llm"].for_user(user_id)
            provider = llm.name
            deep = await llm_deep_dive(llm, meta, summary, idx, transcript)
            async with sm() as db:
                db.add(SectionDeepDive(video_id=video_id, idx=idx, data=deep))
                await db.commit()
        await send_article(bot, chat_id, render.deep_blocks(meta, summary, sec, idx, deep),
                           render.deep_html(meta, sec, idx, deep),
                           reply_markup=kb.sections_picker(video_id, summary["sections"]))
        try:
            await bot.delete_message(chat_id, status_message_id)
        except TelegramBadRequest:
            pass
        await finish(True, provider=provider)
    except LLMError as e:
        await _status(bot, chat_id, status_message_id, f"❌ Не получилось разобрать часть: {e}")
        await finish(False, repr(e), provider)
    except Exception as e:  # noqa: BLE001
        log.exception("deep dive %s/%s failed", video_id, idx)
        await _status(bot, chat_id, status_message_id, "❌ Не получилось разобрать часть.")
        await finish(False, repr(e), provider)


def _esc(t: str) -> str:
    import html
    return html.escape(t, quote=True)
