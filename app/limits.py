"""Месячные лимиты (скользящие 30 дней): конспекты и разборы — одна корзина, скачивания — другая.
Владельцы — без лимитов. Неудачные задачи не считаются."""
from datetime import timedelta

from sqlalchemy import func, select

from app.config import Settings
from app.db.models import Job, utcnow

BUCKETS = {
    "summary": (("youtube_summary", "deep_dive"), "конспектов и разборов"),
    "download": (("youtube_video",), "скачиваний"),
}
WINDOW = timedelta(days=30)


def limit_for(settings: Settings, bucket: str) -> int:
    return settings.monthly_summaries if bucket == "summary" else settings.monthly_downloads


async def used(s, user_id: int, bucket: str) -> tuple[int, object]:
    kinds = BUCKETS[bucket][0]
    since = utcnow() - WINDOW
    q = select(func.count(), func.min(Job.created_at)).where(
        Job.user_id == user_id, Job.kind.in_(kinds), Job.created_at >= since, Job.status != "failed")
    n, oldest = (await s.execute(q)).one()
    return n, oldest


async def check(s, settings: Settings, user_id: int, bucket: str, n: int = 1) -> str | None:
    """None — можно; иначе текст для пользователя."""
    if user_id in settings.owner_ids:
        return None
    lim = limit_for(settings, bucket)
    cnt, oldest = await used(s, user_id, bucket)
    if cnt + n <= lim:
        return None
    msg = f"Лимит: {lim} {BUCKETS[bucket][1]} за 30 дней, использовано {cnt}."
    if oldest:
        msg += f" Следующий освободится {(oldest + WINDOW):%d.%m}."
    return msg


async def usage_text(s, settings: Settings, user_id: int) -> str:
    if user_id in settings.owner_ids:
        return "Лимиты: без ограничений (владелец)"
    parts = []
    for b in BUCKETS:
        cnt, _ = await used(s, user_id, b)
        parts.append(f"{BUCKETS[b][1]} {cnt}/{limit_for(settings, b)}")
    return "За 30 дней: " + ", ".join(parts)
