import json

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import CallbackQuery, Message
from arq.connections import ArqRedis

from app.bot import keyboards as kb
from app.config import Settings
from app.db import repo
from app.services.downloader import AUDIO_DEFAULTS, QUALITY_LABELS
from app.services.youtube import extract_video_ids

router = Router()

HELP = (
    "Пришлите ссылку на YouTube-видео — я верну:\n"
    "• 📝 конспект с ключевыми мыслями;\n"
    "• 📄 полную расшифровку файлом;\n"
    "• ⬇️ кнопку, чтобы скачать само видео или звук.\n\n"
    "/settings — качество и аудиодорожка по умолчанию.\n"
    "Скоро: статьи и голосовые сообщения."
)


@router.message(CommandStart())
async def start(m: Message):
    await m.answer("Привет! " + HELP + f"\n\nВаш ID: <code>{m.from_user.id}</code>")


@router.message(Command("help"))
async def help_(m: Message):
    await m.answer(HELP)


async def _quota_ok(s, settings: Settings, user_id: int, n: int = 1) -> bool:
    if user_id in settings.owner_ids:
        return True
    return await repo.jobs_last_24h(s, user_id) + n <= settings.daily_limit


# ---------- настройки ----------

@router.message(Command("settings"))
async def settings_cmd(m: Message, settings: Settings, sessionmaker):
    async with sessionmaker() as s:
        await repo.upsert_user(s, m.from_user)
        us = await repo.get_user_settings(s, m.from_user.id, settings.default_quality, settings.default_audio)
        await s.commit()
    await m.answer(kb.settings_text(us.quality, us.audio), reply_markup=kb.settings_kb(us.quality, us.audio))


@router.callback_query(F.data.startswith("set:"))
async def settings_cb(c: CallbackQuery, settings: Settings, sessionmaker):
    _, what, val = c.data.split(":", 2)
    if (what == "q" and val not in QUALITY_LABELS) or (what == "a" and val not in AUDIO_DEFAULTS):
        await c.answer()
        return
    async with sessionmaker() as s:
        await repo.upsert_user(s, c.from_user)
        us = await repo.set_user_setting(s, c.from_user.id, settings.default_quality, settings.default_audio,
                                         quality=val if what == "q" else None, audio=val if what == "a" else None)
        await s.commit()
    await c.message.edit_text(kb.settings_text(us.quality, us.audio), reply_markup=kb.settings_kb(us.quality, us.audio))
    await c.answer("Сохранено")


# ---------- скачивание ----------

async def _enqueue_download(c: CallbackQuery, settings: Settings, sessionmaker, arq: ArqRedis,
                            video_id: str, quality: str, audio: str):
    async with sessionmaker() as s:
        await repo.upsert_user(s, c.from_user)
        if quality == "d" or audio == "d":
            us = await repo.get_user_settings(s, c.from_user.id, settings.default_quality, settings.default_audio)
            quality = us.quality if quality == "d" else quality
            audio = us.audio if audio == "d" else audio
        if not await _quota_ok(s, settings, c.from_user.id):
            await s.commit()
            await c.answer(f"Лимит — {settings.daily_limit} запросов за 24 часа.", show_alert=True)
            return
        job = await repo.create_job(s, c.from_user.id, f"{video_id}:{quality}:{audio}", kind="youtube_video")
        await s.commit()
    await c.answer("Скачиваю…")
    status = await c.message.answer(f"⏳ Скачивание в очереди ({QUALITY_LABELS.get(quality, quality)})…")
    await arq.enqueue_job(
        "download_video", job_id=job.id, user_id=c.from_user.id, chat_id=c.message.chat.id,
        video_id=video_id, quality=quality, audio=audio, status_message_id=status.message_id,
    )


@router.callback_query(F.data.startswith("dl:"))
async def download_cb(c: CallbackQuery, settings: Settings, sessionmaker, arq: ArqRedis):
    _, vid, quality, audio = c.data.split(":", 3)
    await _enqueue_download(c, settings, sessionmaker, arq, vid, quality, audio)


@router.callback_query(F.data.startswith("pick:"))
async def pick_cb(c: CallbackQuery, arq: ArqRedis):
    vid = c.data.split(":", 1)[1]
    await c.answer("Смотрю, какие есть варианты…")
    status = await c.message.answer("🔎 Смотрю доступные качества…")
    await arq.enqueue_job("probe_video", chat_id=c.message.chat.id, video_id=vid,
                          status_message_id=status.message_id)


@router.callback_query(F.data.startswith("q:"))
async def quality_cb(c: CallbackQuery, settings: Settings, sessionmaker, arq: ArqRedis):
    _, vid, quality = c.data.split(":", 2)
    raw = await arq.get(f"probe:{vid}")
    probe = json.loads(raw) if raw else {}
    size = probe.get("sizes", {}).get(quality, 0)
    if size > settings.max_upload_bytes:
        await c.answer("Это качество больше лимита Telegram — выберите пониже.", show_alert=True)
        return
    tracks = probe.get("audio_tracks", [])
    if len(tracks) > 1:
        from app.services.downloader import AudioTrack
        await c.message.edit_text(
            f"🎧 Выберите аудиодорожку ({QUALITY_LABELS.get(quality, quality)}):",
            reply_markup=kb.audio_picker(vid, quality, [AudioTrack(**t) for t in tracks]))
        await c.answer()
        return
    await _enqueue_download(c, settings, sessionmaker, arq, vid, quality, "orig")


# ---------- ссылки (последним: ловит любой текст) ----------

@router.message(F.text | F.caption)
async def on_text(m: Message, settings: Settings, sessionmaker, arq: ArqRedis):
    ids = extract_video_ids(m.text or m.caption or "")
    if not ids:
        await m.answer("Пока я понимаю только ссылки на YouTube. " + HELP)
        return
    ids = ids[:3]  # не больше трёх видео за одно сообщение
    user = m.from_user

    async with sessionmaker() as s:
        await repo.upsert_user(s, user)
        if not await _quota_ok(s, settings, user.id, len(ids)):
            await s.commit()
            await m.answer(f"Лимит — {settings.daily_limit} запросов за 24 часа. Попробуйте позже.")
            return
        jobs = [await repo.create_job(s, user.id, vid) for vid in ids]
        await s.commit()

    for job, vid in zip(jobs, ids):
        status = await m.reply("⏳ В очереди…")
        await arq.enqueue_job(
            "process_youtube",
            job_id=job.id, user_id=user.id, chat_id=m.chat.id,
            video_id=vid, status_message_id=status.message_id,
            reply_to=m.message_id,
        )
