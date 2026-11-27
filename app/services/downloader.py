"""Скачивание видео/аудио с YouTube через yt-dlp.

Против блокировок: PO-токены (сервис bgutil) + JS-рантайм node + опционально прокси.
"""
import asyncio
import logging
import os
import shutil
import time
from dataclasses import dataclass, field
from typing import Callable

import yt_dlp

log = logging.getLogger(__name__)

QUALITIES = ["360", "480", "720", "1080", "best", "audio"]
QUALITY_LABELS = {"360": "360p", "480": "480p", "720": "720p", "1080": "1080p",
                  "best": "Максимум", "audio": "Только звук"}
AUDIO_DEFAULTS = {"orig": "Оригинал", "ru": "Русская", "en": "Английская"}


class DownloadError(Exception):
    """Ошибка, которую можно показать пользователю."""
    direct_blocked: bool = False


class Blocked(DownloadError):
    pass


@dataclass
class AudioTrack:
    lang: str
    name: str
    is_original: bool


@dataclass
class ProbeResult:
    video_id: str
    title: str
    duration: int
    sizes: dict[str, int] = field(default_factory=dict)  # quality -> примерный размер в байтах
    audio_tracks: list[AudioTrack] = field(default_factory=list)


@dataclass
class DownloadedFile:
    path: str
    is_audio: bool
    title: str
    duration: int
    width: int | None
    height: int | None
    size: int
    via_proxy: bool = False
    direct_blocked: bool = False


def format_selector(quality: str, audio: str) -> str:
    """Строка выбора форматов yt-dlp. audio: 'orig' или код языка."""
    if audio == "orig":
        # yt-dlp по умолчанию ставит оригинальную дорожку первой
        audios = ["ba[ext=m4a]", "ba"]
    else:
        audios = [f"ba[language^={audio}][ext=m4a]", f"ba[language^={audio}]", "ba[ext=m4a]", "ba"]
    if quality == "audio":
        return "/".join(audios)
    if quality == "best":
        videos, fallback = ["bv*"], "b"
    else:
        h = int(quality)
        videos = [f"bv*[height<={h}][vcodec^=avc1]", f"bv*[height<={h}]"]
        fallback = f"b[height<={h}]/b"
    return "/".join(f"{v}+{a}" for v in videos for a in audios) + "/" + fallback


def _fmt_size(f: dict, duration: float) -> int:
    size = f.get("filesize") or f.get("filesize_approx")
    if not size and f.get("tbr") and duration:
        size = f["tbr"] * 1000 / 8 * duration
    return int(size or 0)


def summarize_formats(info: dict) -> ProbeResult:
    formats = info.get("formats") or []
    duration = int(info.get("duration") or 0)
    audios = [f for f in formats if f.get("vcodec") == "none" and f.get("acodec") not in (None, "none")]
    videos = [f for f in formats if f.get("vcodec") not in (None, "none") and f.get("height")]

    # аудиодорожки (у видео с дубляжом их несколько)
    tracks: dict[str, AudioTrack] = {}
    for f in audios:
        lang = f.get("language")
        if not lang:
            continue
        note = (f.get("format_note") or "").lower()
        is_orig = "original" in note or (f.get("language_preference") or 0) >= 10
        name = (f.get("format_note") or lang).split(",")[0].replace("original", "").replace("(default)", "").strip() or lang
        prev = tracks.get(lang)
        tracks[lang] = AudioTrack(lang, name, is_orig or (prev.is_original if prev else False))
    track_list = sorted(tracks.values(), key=lambda t: (not t.is_original, t.lang))

    best_audio = max((_fmt_size(f, duration) for f in audios if f.get("ext") == "m4a"), default=0) \
        or max((_fmt_size(f, duration) for f in audios), default=0)
    max_h = max((f["height"] for f in videos), default=0)
    sizes: dict[str, int] = {}
    for q in ("360", "480", "720", "1080"):
        h = int(q)
        if max_h < h and q != "360":
            continue
        cands = [f for f in videos if f["height"] <= h]
        avc = [f for f in cands if str(f.get("vcodec", "")).startswith("avc1")] or cands
        if avc:
            best = max(avc, key=lambda f: (f["height"], f.get("tbr") or 0))
            sizes[q] = _fmt_size(best, duration) + (best_audio if best.get("acodec") in (None, "none") else 0)
    if videos:
        top = max(videos, key=lambda f: (f["height"], f.get("tbr") or 0))
        sizes["best"] = _fmt_size(top, duration) + best_audio
    sizes["audio"] = best_audio
    return ProbeResult(info.get("id", ""), info.get("title") or "", duration, sizes, track_list)


class Downloader:
    def __init__(self, downloads_dir: str, max_bytes: int, bgutil_url: str | None = None,
                 proxy: str | None = None):
        self.dir = downloads_dir
        self.max_bytes = max_bytes
        self.bgutil_url = bgutil_url
        self.proxy = proxy
        os.makedirs(self.dir, exist_ok=True)

    def _opts(self, use_proxy: bool) -> dict:
        o: dict = {
            "quiet": True, "no_warnings": True, "noprogress": True, "noplaylist": True,
            "js_runtimes": {"node": {"path": None}},
            "retries": 5, "fragment_retries": 10, "concurrent_fragment_downloads": 4,
        }
        if self.bgutil_url:
            o["extractor_args"] = {"youtubepot-bgutilhttp": {"base_url": [self.bgutil_url]}}
        if use_proxy and self.proxy:
            o["proxy"] = self.proxy
        return o

    def _with_fallback(self, fn):
        """Сначала напрямую (быстро и бесплатно), при отказе YouTube — через прокси.
        Возвращает (результат, через_прокси, напрямую_заблокировано)."""
        try:
            return fn(False), False, False
        except yt_dlp.utils.DownloadError as e:
            err = _user_error(e)
            blocked = isinstance(err, Blocked)
            if not self.proxy:
                err.direct_blocked = blocked
                raise err from e
            log.warning("direct yt-dlp failed (%s), retrying via proxy", e)
            try:
                return fn(True), True, blocked
            except yt_dlp.utils.DownloadError as e2:
                err2 = _user_error(e2)
                err2.direct_blocked = blocked
                raise err2 from e2

    # --- probe ---
    def _probe_sync(self, video_id: str) -> ProbeResult:
        def run(use_proxy):
            with yt_dlp.YoutubeDL(self._opts(use_proxy)) as ydl:
                return ydl.extract_info(_url(video_id), download=False)
        return summarize_formats(self._with_fallback(run)[0])

    async def probe(self, video_id: str) -> ProbeResult:
        return await asyncio.to_thread(self._probe_sync, video_id)

    # --- download ---
    def _download_sync(self, video_id: str, quality: str, audio: str,
                       on_progress: Callable[[int, int], None] | None) -> DownloadedFile:
        def run(use_proxy):
            o = self._opts(use_proxy)
            o["format"] = format_selector(quality, audio)
            o["outtmpl"] = os.path.join(self.dir, f"{video_id}-{quality}-{audio}-{int(time.time())}.%(ext)s")
            if quality == "audio":
                o["postprocessors"] = [{"key": "FFmpegExtractAudio", "preferredcodec": "m4a"}]
            else:
                o["merge_output_format"] = "mp4"
            if on_progress:
                o["progress_hooks"] = [lambda d: d.get("status") == "downloading" and on_progress(
                    int(d.get("downloaded_bytes") or 0),
                    int(d.get("total_bytes") or d.get("total_bytes_estimate") or 0))]
            with yt_dlp.YoutubeDL(o) as ydl:
                info = ydl.extract_info(_url(video_id), download=False)
                est = sum(_fmt_size(f, info.get("duration") or 0) for f in (info.get("requested_formats") or [info]))
                if est > self.max_bytes:
                    raise TooBig(est)
                info = ydl.process_ie_result(info, download=True)
            req = (info.get("requested_downloads") or [{}])[0]
            path = req.get("filepath") or req.get("_filename")
            if not path or not os.path.exists(path):
                raise DownloadError("Файл не скачался.")
            size = os.path.getsize(path)
            if size > self.max_bytes:
                os.remove(path)
                raise TooBig(size)
            return DownloadedFile(path, quality == "audio", info.get("title") or video_id,
                                  int(info.get("duration") or 0), info.get("width"), info.get("height"), size)
        f, via_proxy, blocked = self._with_fallback(run)
        f.via_proxy, f.direct_blocked = via_proxy, blocked
        return f

    async def download(self, video_id: str, quality: str, audio: str,
                       on_progress: Callable[[int, int], None] | None = None) -> DownloadedFile:
        return await asyncio.to_thread(self._download_sync, video_id, quality, audio, on_progress)

    def cleanup(self, older_than_s: int = 3600) -> None:
        now = time.time()
        for name in os.listdir(self.dir):
            p = os.path.join(self.dir, name)
            try:
                if now - os.path.getmtime(p) >= older_than_s:
                    shutil.rmtree(p) if os.path.isdir(p) else os.remove(p)
            except OSError:
                pass


class TooBig(DownloadError):
    def __init__(self, size: int):
        self.size = size
        super().__init__(f"Файл получается {human_size(size)} — больше лимита Telegram. Выберите качество ниже.")


def _url(video_id: str) -> str:
    return f"https://www.youtube.com/watch?v={video_id}"


def _user_error(e: Exception) -> DownloadError:
    msg = str(e)
    if "Sign in to confirm" in msg or "bot" in msg.lower() or "403" in msg:
        return Blocked("YouTube заблокировал скачивание с сервера. Нужен прокси.")
    if "Private video" in msg or "unavailable" in msg.lower():
        return DownloadError("Видео недоступно (удалено или приватное).")
    if "age" in msg.lower() and "confirm" in msg.lower():
        return DownloadError("Видео с возрастным ограничением — скачать нельзя.")
    if "live" in msg.lower():
        return DownloadError("Прямые эфиры скачивать нельзя — дождитесь записи.")
    return DownloadError("Не удалось скачать видео.")


def human_size(n: int) -> str:
    if n >= 1024 ** 3:
        return f"{n / 1024 ** 3:.1f} ГБ"
    return f"{max(1, round(n / 1024 ** 2))} МБ"
