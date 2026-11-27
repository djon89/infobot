from datetime import datetime, timezone

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import Channel, Digest, DigestSettings, FeedVideo, UserChannel, VideoBlurb, utcnow


async def follow(s: AsyncSession, user_id: int, channel_id: str, title: str, source: str = "manual") -> bool:
    """True, если подписка новая."""
    ch = await s.get(Channel, channel_id)
    if ch is None:
        s.add(Channel(id=channel_id, title=title))
    elif title and title != channel_id:
        ch.title = title
    await s.flush()
    if await s.get(UserChannel, (user_id, channel_id)):
        return False
    s.add(UserChannel(user_id=user_id, channel_id=channel_id, source=source))
    await s.flush()
    return True


async def unfollow(s: AsyncSession, user_id: int, channel_id: str) -> None:
    await s.execute(delete(UserChannel).where(UserChannel.user_id == user_id, UserChannel.channel_id == channel_id))


async def user_channels(s: AsyncSession, user_id: int) -> list[Channel]:
    q = (select(Channel).join(UserChannel, UserChannel.channel_id == Channel.id)
         .where(UserChannel.user_id == user_id).order_by(func.lower(Channel.title)))
    return list((await s.execute(q)).scalars())


async def channels_to_poll(s: AsyncSession, user_id: int | None = None) -> list[str]:
    q = select(UserChannel.channel_id).distinct()
    if user_id is not None:
        q = q.where(UserChannel.user_id == user_id)
    return list((await s.execute(q)).scalars())


async def save_entries(s: AsyncSession, entries) -> int:
    new = 0
    for e in entries:
        v = await s.get(FeedVideo, e.video_id)
        if v is None:
            s.add(FeedVideo(video_id=e.video_id, channel_id=e.channel_id, title=e.title, published_at=e.published))
            new += 1
        else:
            v.title = e.title
        if getattr(e, "description", None) and not await s.get(VideoBlurb, e.video_id):
            s.add(VideoBlurb(video_id=e.video_id, description=e.description[:2000]))
    return new


async def blurbs_for(s: AsyncSession, video_ids: list[str]) -> dict[str, VideoBlurb]:
    if not video_ids:
        return {}
    rows = (await s.execute(select(VideoBlurb).where(VideoBlurb.video_id.in_(video_ids)))).scalars()
    return {b.video_id: b for b in rows}


async def save_blurbs(s: AsyncSession, lines: dict[str, str]) -> None:
    for vid, line in lines.items():
        b = await s.get(VideoBlurb, vid)
        if b is None:
            s.add(VideoBlurb(video_id=vid, blurb=line))
        else:
            b.blurb = line


async def get_digest_settings(s: AsyncSession, user_id: int, default_tz: str, default_hour: int | None) -> DigestSettings:
    ds = await s.get(DigestSettings, user_id)
    if ds is None:
        ds = DigestSettings(user_id=user_id, hour=default_hour, tz=default_tz, cursor=utcnow())
        s.add(ds)
        await s.flush()
    return ds


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


async def new_videos_for(s: AsyncSession, user_id: int, since: datetime, limit: int = 200):
    """Видео каналов пользователя, вышедшие после since. [(FeedVideo, Channel)] от новых к старым."""
    q = (select(FeedVideo, Channel)
         .join(Channel, Channel.id == FeedVideo.channel_id)
         .join(UserChannel, (UserChannel.channel_id == FeedVideo.channel_id) & (UserChannel.user_id == user_id))
         .where(FeedVideo.published_at > since)
         .order_by(FeedVideo.published_at.desc()).limit(limit))
    return [(v, c) for v, c in (await s.execute(q)).all()]


async def latest_for_channel(s: AsyncSession, channel_id: str, n: int = 5):
    q = (select(FeedVideo, Channel).join(Channel, Channel.id == FeedVideo.channel_id)
         .where(FeedVideo.channel_id == channel_id).order_by(FeedVideo.published_at.desc()).limit(n))
    return [(v, c) for v, c in (await s.execute(q)).all()]


async def create_digest(s: AsyncSession, user_id: int, video_ids: list[str]) -> Digest:
    d = Digest(user_id=user_id, video_ids=video_ids)
    s.add(d)
    await s.flush()
    return d


async def digest_items(s: AsyncSession, digest: Digest):
    if not digest.video_ids:
        return []
    q = (select(FeedVideo, Channel).join(Channel, Channel.id == FeedVideo.channel_id)
         .where(FeedVideo.video_id.in_(digest.video_ids)))
    rows = {v.video_id: (v, c) for v, c in (await s.execute(q)).all()}
    return [rows[v] for v in digest.video_ids if v in rows]
