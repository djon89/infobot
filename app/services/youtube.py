import re
from dataclasses import dataclass

import httpx

_ID = r"([A-Za-z0-9_-]{11})"
_PATTERNS = [
    re.compile(rf"(?:https?://)?(?:www\.|m\.|music\.)?youtube\.com/watch\?(?:[^\s#]*&)?v={_ID}"),
    re.compile(rf"(?:https?://)?(?:www\.|m\.)?youtube\.com/(?:shorts|live|embed|v)/{_ID}"),
    re.compile(rf"(?:https?://)?youtu\.be/{_ID}"),
]


def extract_video_ids(text: str) -> list[str]:
    """Все YouTube video_id из текста, в порядке появления, без дублей."""
    found: list[tuple[int, str]] = []
    for p in _PATTERNS:
        found += [(m.start(), m.group(1)) for m in p.finditer(text)]
    seen, result = set(), []
    for _, vid in sorted(found):
        if vid not in seen:
            seen.add(vid)
            result.append(vid)
    return result


@dataclass
class VideoMeta:
    video_id: str
    title: str
    author: str | None = None

    @property
    def url(self) -> str:
        return f"https://www.youtube.com/watch?v={self.video_id}"


async def fetch_meta(video_id: str, proxy: str | None = None) -> VideoMeta:
    """Название и автор через oEmbed (без API-ключа). При ошибке — заглушка."""
    url = f"https://www.youtube.com/watch?v={video_id}"
    try:
        async with httpx.AsyncClient(timeout=15, proxy=proxy) as c:
            r = await c.get("https://www.youtube.com/oembed", params={"url": url, "format": "json"})
            r.raise_for_status()
            d = r.json()
            return VideoMeta(video_id, d.get("title") or video_id, d.get("author_name"))
    except Exception:
        return VideoMeta(video_id, video_id)
