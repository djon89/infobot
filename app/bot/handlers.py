from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import Message
from arq.connections import ArqRedis

from app.config import Settings
from app.db import repo
from app.services.youtube import extract_video_ids

router = Router()

HELP = (
    "Пришлите ссылку на YouTube-видео — я верну:\n"
    "• 📝 конспект с ключевыми мыслями;\n"
    "• 📄 полную расшифровку файлом.\n\n"
    "Скоро: статьи, голосовые сообщения, скачивание видео."
)


@router.message(CommandStart())
async def start(m: Message):
    await m.answer("Привет! " + HELP)


@router.message(Command("help"))
async def help_(m: Message):
    await m.answer(HELP)


@router.message(F.text | F.caption)
async def on_text(m: Message, settings: Settings, sessionmaker, arq: ArqRedis):
    ids = extract_video_ids(m.text or m.caption or "")
    if not ids:
        await m.answer("Пока я понимаю только ссылки на YouTube. " + HELP)
        return
    ids = ids[:3]  # не больше трёх видео за одно сообщение
    user = m.from_user
    is_owner = user.id in settings.owner_ids

    async with sessionmaker() as s:
        await repo.upsert_user(s, user)
        if not is_owner:
            used = await repo.jobs_last_24h(s, user.id)
            if used + len(ids) > settings.daily_limit:
                await s.commit()
                await m.answer(f"Лимит — {settings.daily_limit} видео за 24 часа. Попробуйте позже.")
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
