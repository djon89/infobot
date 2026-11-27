from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.transcript.base import Segment, Transcript
from app.services.youtube import VideoMeta

from .models import Job, User, UserSettings, Video, VideoFile, utcnow


async def upsert_user(s: AsyncSession, tg_user) -> User:
    u = await s.get(User, tg_user.id)
    if u is None:
        u = User(id=tg_user.id)
        s.add(u)
    u.username = tg_user.username
    u.first_name = tg_user.first_name
    u.language_code = tg_user.language_code
    await s.flush()  # пользователь должен попасть в БД раньше его задач (внешний ключ)
    return u


async def jobs_last_24h(s: AsyncSession, user_id: int) -> int:
    since = datetime.now(timezone.utc) - timedelta(hours=24)
    q = select(func.count()).select_from(Job).where(
        Job.user_id == user_id, Job.created_at >= since, Job.status != "failed"
    )
    return (await s.execute(q)).scalar_one()


async def create_job(s: AsyncSession, user_id: int, target: str, kind: str = "youtube_summary") -> Job:
    j = Job(user_id=user_id, target=target, kind=kind)
    s.add(j)
    await s.flush()
    return j


async def finish_job(s: AsyncSession, job_id: int, ok: bool, provider: str | None = None, error: str | None = None):
    j = await s.get(Job, job_id)
    if j:
        j.status = "done" if ok else "failed"
        j.llm_provider = provider
        j.error = error
        j.finished_at = utcnow()


def video_transcript(v: Video) -> Transcript | None:
    if not v or v.segments is None:
        return None
    return Transcript(v.id, v.language or "?", bool(v.is_generated),
                      [Segment(a, b) for a, b in v.segments], v.transcript_source or "cache")


async def save_transcript(s: AsyncSession, meta: VideoMeta, t: Transcript) -> Video:
    v = await s.get(Video, meta.video_id) or Video(id=meta.video_id)
    v.title, v.author = meta.title, meta.author
    v.language, v.is_generated, v.transcript_source = t.language, t.is_generated, t.source
    v.segments = [[seg.start, seg.text] for seg in t.segments]
    s.add(v)
    return v


async def get_user_settings(s: AsyncSession, user_id: int, default_quality: str, default_audio: str) -> UserSettings:
    us = await s.get(UserSettings, user_id)
    return us or UserSettings(user_id=user_id, quality=default_quality, audio=default_audio)


async def set_user_setting(s: AsyncSession, user_id: int, default_quality: str, default_audio: str,
                           quality: str | None = None, audio: str | None = None) -> UserSettings:
    us = await s.get(UserSettings, user_id)
    if us is None:
        us = UserSettings(user_id=user_id, quality=default_quality, audio=default_audio)
        s.add(us)
    if quality:
        us.quality = quality
    if audio:
        us.audio = audio
    return us


async def find_video_file(s: AsyncSession, video_id: str, quality: str, audio: str) -> VideoFile | None:
    q = select(VideoFile).where(VideoFile.video_id == video_id, VideoFile.quality == quality,
                                VideoFile.audio == audio).order_by(VideoFile.id.desc()).limit(1)
    return (await s.execute(q)).scalar_one_or_none()
