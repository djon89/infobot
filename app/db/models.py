from datetime import datetime, timezone

from sqlalchemy import JSON, BigInteger, Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)  # telegram user id
    username: Mapped[str | None] = mapped_column(String(64))
    first_name: Mapped[str | None] = mapped_column(String(128))
    language_code: Mapped[str | None] = mapped_column(String(16))
    is_blocked: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Video(Base):
    """Кэш по видео: один и тот же ролик от разных людей обрабатывается один раз."""
    __tablename__ = "videos"
    id: Mapped[str] = mapped_column(String(16), primary_key=True)  # youtube video id
    title: Mapped[str] = mapped_column(Text)
    author: Mapped[str | None] = mapped_column(Text)
    language: Mapped[str | None] = mapped_column(String(16))
    is_generated: Mapped[bool | None] = mapped_column(Boolean)
    transcript_source: Mapped[str | None] = mapped_column(String(64))
    segments: Mapped[list | None] = mapped_column(JSON)  # [[start, text], ...]
    summary: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Job(Base):
    __tablename__ = "jobs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"), index=True)
    kind: Mapped[str] = mapped_column(String(32), default="youtube_summary")
    target: Mapped[str] = mapped_column(Text)  # video id / url
    status: Mapped[str] = mapped_column(String(16), default="queued")  # queued|done|failed
    llm_provider: Mapped[str | None] = mapped_column(String(32))
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class UserSettings(Base):
    __tablename__ = "user_settings"
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"), primary_key=True)
    quality: Mapped[str] = mapped_column(String(16))
    audio: Mapped[str] = mapped_column(String(16))


class VideoFile(Base):
    """Уже загруженные в Telegram файлы: повторная отправка мгновенна, без скачивания."""
    __tablename__ = "video_files"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    video_id: Mapped[str] = mapped_column(String(16), index=True)
    quality: Mapped[str] = mapped_column(String(16))
    audio: Mapped[str] = mapped_column(String(16))
    is_audio: Mapped[bool] = mapped_column(Boolean, default=False)
    file_id: Mapped[str] = mapped_column(Text)
    size: Mapped[int | None] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Channel(Base):
    __tablename__ = "channels"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)  # UC...
    title: Mapped[str] = mapped_column(Text)
    checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)


class UserChannel(Base):
    __tablename__ = "user_channels"
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"), primary_key=True)
    channel_id: Mapped[str] = mapped_column(String(32), ForeignKey("channels.id"), primary_key=True)
    source: Mapped[str] = mapped_column(String(16), default="manual")  # manual | youtube
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class FeedVideo(Base):
    __tablename__ = "feed_videos"
    video_id: Mapped[str] = mapped_column(String(16), primary_key=True)
    channel_id: Mapped[str] = mapped_column(String(32), ForeignKey("channels.id"), index=True)
    title: Mapped[str] = mapped_column(Text)
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    found_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class DigestSettings(Base):
    __tablename__ = "digest_settings"
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"), primary_key=True)
    hour: Mapped[int | None] = mapped_column(Integer)  # None = ежедневная сводка выключена
    tz: Mapped[str] = mapped_column(String(64))
    cursor: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))  # до какого момента уже показано
    last_sent_on: Mapped[str | None] = mapped_column(String(10))  # YYYY-MM-DD по местному времени


class Digest(Base):
    __tablename__ = "digests"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"), index=True)
    video_ids: Mapped[list] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class GoogleAccount(Base):
    __tablename__ = "google_accounts"
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"), primary_key=True)
    refresh_token: Mapped[str] = mapped_column(Text)
    synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SectionDeepDive(Base):
    """Кэш подробных разборов частей видео."""
    __tablename__ = "section_deep_dives"
    video_id: Mapped[str] = mapped_column(String(16), primary_key=True)
    idx: Mapped[int] = mapped_column(Integer, primary_key=True)
    data: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class DownloadLog(Base):
    """Журнал скачиваний для /stats: объём, через прокси или напрямую, блокировки."""
    __tablename__ = "download_log"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    video_id: Mapped[str] = mapped_column(String(16))
    quality: Mapped[str] = mapped_column(String(16))
    bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    cached: Mapped[bool] = mapped_column(Boolean, default=False)      # отправлено по file_id без скачивания
    via_proxy: Mapped[bool] = mapped_column(Boolean, default=False)
    direct_blocked: Mapped[bool] = mapped_column(Boolean, default=False)  # напрямую YouTube отказал
    ok: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class BotMeta(Base):
    """Служебные значения бота (например, последняя объявленная версия)."""
    __tablename__ = "bot_meta"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text)


class VideoBlurb(Base):
    """Описание видео из ленты и одна строка «что внутри» для умной сводки (общая для всех)."""
    __tablename__ = "video_blurbs"
    video_id: Mapped[str] = mapped_column(String(16), primary_key=True)
    description: Mapped[str | None] = mapped_column(Text)
    blurb: Mapped[str | None] = mapped_column(Text)


class Post(Base):
    """Пост со списком видео (сводка или страница канала). Номера видео в нём — для команд /sum_<id>_<n>."""
    __tablename__ = "posts"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"), index=True)
    kind: Mapped[str] = mapped_column(String(16))  # digest | channel
    items: Mapped[list] = mapped_column(JSON)      # [{id, title, channel, published, duration, cover}]
    meta: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
