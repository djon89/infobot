import json
import os
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.bot import keyboards as kb
from app.config import Settings
from app.db import repo
from app.db.models import Job, VideoFile
from app.db.session import init_db, make_engine, make_sessionmaker
from app.services.downloader import DownloadedFile, DownloadError, format_selector, summarize_formats
from app.worker import tasks

INFO = {
    "id": "dQw4w9WgXcQ", "title": "Тест", "duration": 600,
    "formats": [
        {"format_id": "140", "vcodec": "none", "acodec": "mp4a", "ext": "m4a", "language": "en",
         "format_note": "English original (default), medium", "filesize": 10_000_000},
        {"format_id": "140-1", "vcodec": "none", "acodec": "mp4a", "ext": "m4a", "language": "ru",
         "format_note": "Russian, medium", "filesize": 10_000_000},
        {"format_id": "136", "vcodec": "avc1.4d401f", "acodec": "none", "height": 720, "filesize": 80_000_000},
        {"format_id": "247", "vcodec": "vp9", "acodec": "none", "height": 720, "filesize": 60_000_000},
        {"format_id": "134", "vcodec": "avc1.4d401e", "acodec": "none", "height": 360, "tbr": 400},
        {"format_id": "401", "vcodec": "av01", "acodec": "none", "height": 2160, "filesize": 900_000_000},
    ],
}


def test_format_selector():
    s = format_selector("720", "ru")
    assert s.startswith("bv*[height<=720][vcodec^=avc1]+ba[language^=ru][ext=m4a]/")
    assert s.endswith("/b[height<=720]/b")
    assert format_selector("audio", "orig") == "ba[ext=m4a]/ba"
    assert format_selector("best", "orig").startswith("bv*+ba[ext=m4a]")


def test_summarize_formats():
    p = summarize_formats(INFO)
    assert [t.lang for t in p.audio_tracks] == ["en", "ru"] and p.audio_tracks[0].is_original
    assert p.sizes["720"] == 90_000_000          # avc1 видео + аудио
    assert p.sizes["360"] == 400 * 1000 / 8 * 600 + 10_000_000  # по битрейту
    assert p.sizes["1080"] == 90_000_000          # 1080 нет — лучшее ≤1080
    assert p.sizes["best"] == 910_000_000
    assert p.sizes["audio"] == 10_000_000


def test_callback_data_fits_64_bytes():
    vid = "dQw4w9WgXcQ"
    p = summarize_formats(INFO)
    boards = [kb.video_actions(vid, "1080", "ru"), kb.quality_picker(vid, p.sizes, 50 * 2**20),
              kb.audio_picker(vid, "1080", p.audio_tracks), kb.settings_kb("720", "orig")]
    for b in boards:
        for row in b.inline_keyboard:
            for btn in row:
                assert len(btn.callback_data.encode()) <= 64
    # слишком большие варианты помечены
    texts = [b.text for row in kb.quality_picker(vid, p.sizes, 50 * 2**20).inline_keyboard for b in row]
    assert any(t.startswith("🚫 Максимум") for t in texts)


class FakeDownloader:
    def __init__(self, tmp):
        self.tmp, self.calls = tmp, 0

    async def download(self, video_id, quality, audio, on_progress=None):
        self.calls += 1
        p = os.path.join(self.tmp, f"{video_id}-{quality}.mp4")
        open(p, "wb").write(b"x" * 1000)
        return DownloadedFile(p, False, 'Видео: "тест"', 60, 1280, 720, 1000)


@pytest.fixture
async def ctx(tmp_path):
    from sqlalchemy import event
    engine = make_engine("sqlite+aiosqlite:///:memory:")
    event.listen(engine.sync_engine, "connect", lambda c, _: c.execute("PRAGMA foreign_keys=ON"))
    await init_db(engine)
    sm = make_sessionmaker(engine)
    async with sm() as s:
        await repo.upsert_user(s, MagicMock(id=1, username="u", first_name="U", language_code="ru"))
        for _ in range(3):
            await repo.create_job(s, 1, "x", kind="youtube_video")
        await s.commit()
    bot = AsyncMock()
    bot.send_video.return_value = MagicMock(video=MagicMock(file_id="FILE123"))
    yield {"bot": bot, "sessionmaker": sm, "settings": Settings(bot_token="1:x"),
           "downloader": FakeDownloader(str(tmp_path)), "redis": AsyncMock(), "tmp": tmp_path}
    await engine.dispose()


async def test_download_flow_and_file_id_cache(ctx):
    kw = dict(user_id=1, chat_id=10, video_id="dQw4w9WgXcQ", quality="720", audio="orig", status_message_id=5)
    await tasks.download_video(ctx, job_id=1, **kw)
    sent = ctx["bot"].send_video.await_args
    assert os.path.basename(sent.args[1].path) == "Видео тест.mp4"   # человеческое имя
    assert sent.kwargs["supports_streaming"] and sent.kwargs["height"] == 720
    assert os.listdir(ctx["tmp"]) == []                                # файл удалён после отправки
    # второй раз — по file_id, без скачивания
    await tasks.download_video(ctx, job_id=2, **kw)
    assert ctx["downloader"].calls == 1
    assert ctx["bot"].send_video.await_args.args[1] == "FILE123"
    async with ctx["sessionmaker"]() as s:
        assert (await s.get(Job, 2)).status == "done"


async def test_local_api_sends_by_path(ctx):
    ctx["settings"] = Settings(bot_token="1:x", telegram_api_url="http://telegram-bot-api:8081")
    await tasks.download_video(ctx, job_id=1, user_id=1, chat_id=10, video_id="abc", quality="720",
                               audio="orig", status_message_id=5)
    assert ctx["bot"].send_video.await_args.args[1].startswith("file:///")


async def test_download_error(ctx):
    ctx["downloader"].download = AsyncMock(side_effect=DownloadError("YouTube заблокировал скачивание с сервера. Нужен прокси."))
    await tasks.download_video(ctx, job_id=1, user_id=1, chat_id=10, video_id="abc", quality="720",
                               audio="orig", status_message_id=5)
    assert "Нужен прокси" in ctx["bot"].edit_message_text.await_args.args[0]
    async with ctx["sessionmaker"]() as s:
        assert (await s.get(Job, 1)).status == "failed"


async def test_probe_stores_and_shows_picker(ctx):
    ctx["downloader"].probe = AsyncMock(return_value=summarize_formats(INFO))
    await tasks.probe_video(ctx, chat_id=10, video_id="dQw4w9WgXcQ", status_message_id=5)
    key, raw = ctx["redis"].set.await_args.args
    assert key == "probe:dQw4w9WgXcQ" and len(json.loads(raw)["audio_tracks"]) == 2
    assert ctx["bot"].edit_message_text.await_args.kwargs["reply_markup"] is not None
