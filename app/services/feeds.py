"""Каналы YouTube: распознавание ссылок, определение channel_id, лента новых видео.

Новые видео берём из RSS плейлиста UULF<id> — это вкладка «Видео» канала:
только полноценные ролики, без Shorts и прямых эфиров. Без API-ключей и квот.
"""
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime

import httpx

UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130 Safari/537.36",
      "Accept-Language": "en-US,en;q=0.9"}
COOKIES = {"CONSENT": "YES+1", "SOCS": "CAI"}  # пропустить страницу согласия на cookies в ЕС

_CHANNEL_PATTERNS = [
    re.compile(r"(?:https?://)?(?:www\.|m\.)?youtube\.com/(channel/UC[\w-]{22})"),
    re.compile(r"(?:https?://)?(?:www\.|m\.)?youtube\.com/(@[\w.\-%]+)"),
    re.compile(r"(?:https?://)?(?:www\.|m\.)?youtube\.com/((?:c|user)/[\w.\-%]+)"),
]
_NS = {"a": "http://www.w3.org/2005/Atom", "yt": "http://www.youtube.com/xml/schemas/2015",
       "media": "http://search.yahoo.com/mrss/"}


class FeedError(Exception):
    pass


def extract_channel_ref(text: str) -> str | None:
    """Ссылка на канал → нормализованный URL канала (или None)."""
    for p in _CHANNEL_PATTERNS:
        m = p.search(text)
        if m:
            return f"https://www.youtube.com/{m.group(1)}"
    return None


@dataclass
class ChannelInfo:
    id: str
    title: str


@dataclass
class FeedEntry:
    video_id: str
    channel_id: str
    title: str
    published: datetime


def parse_channel_page(html: str) -> ChannelInfo | None:
    m = (re.search(r'<meta itemprop="identifier" content="(UC[\w-]{22})"', html)
         or re.search(r'"externalId":"(UC[\w-]{22})"', html)
         or re.search(r'<link rel="canonical" href="https://www\.youtube\.com/channel/(UC[\w-]{22})"', html))
    if not m:
        return None
    t = re.search(r'<meta property="og:title" content="([^"]*)"', html) or re.search(r"<title>(.*?)( - YouTube)?</title>", html)
    import html as h
    return ChannelInfo(m.group(1), h.unescape(t.group(1)) if t else m.group(1))


def parse_feed(xml_text: str, channel_id: str) -> list[FeedEntry]:
    root = ET.fromstring(xml_text)
    out = []
    for e in root.findall("a:entry", _NS):
        vid = e.findtext("yt:videoId", namespaces=_NS)
        pub = e.findtext("a:published", namespaces=_NS)
        if not vid or not pub:
            continue
        out.append(FeedEntry(vid, e.findtext("yt:channelId", default=channel_id, namespaces=_NS),
                             e.findtext("a:title", default=vid, namespaces=_NS),
                             datetime.fromisoformat(pub.replace("Z", "+00:00"))))
    return out


def feed_title(xml_text: str) -> str | None:
    try:
        return ET.fromstring(xml_text).findtext("a:author/a:name", namespaces=_NS) or \
            ET.fromstring(xml_text).findtext("a:title", namespaces=_NS)
    except ET.ParseError:
        return None


class FeedClient:
    def __init__(self, proxy: str | None = None):
        self.proxy = proxy

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=20, headers=UA, cookies=COOKIES, proxy=self.proxy, follow_redirects=True)

    async def resolve(self, channel_url: str) -> ChannelInfo:
        m = re.search(r"/channel/(UC[\w-]{22})", channel_url)
        async with self._client() as c:
            if m:  # id уже есть — название возьмём из ленты
                cid = m.group(1)
                r = await c.get("https://www.youtube.com/feeds/videos.xml", params={"channel_id": cid})
                if r.status_code == 404:
                    raise FeedError("Канал не найден.")
                return ChannelInfo(cid, feed_title(r.text) or cid)
            r = await c.get(channel_url)
            if r.status_code == 404:
                raise FeedError("Канал не найден.")
            info = parse_channel_page(r.text)
            if not info:
                raise FeedError("Не удалось распознать канал по ссылке.")
            return info

    async def uploads(self, channel_id: str) -> list[FeedEntry]:
        """Последние ~15 полноценных видео канала (без Shorts и эфиров)."""
        async with self._client() as c:
            r = await c.get("https://www.youtube.com/feeds/videos.xml",
                            params={"playlist_id": "UULF" + channel_id[2:]})
            if r.status_code == 404:
                return []  # у канала нет обычных видео (только Shorts) — это не ошибка
            if r.status_code != 200:
                raise FeedError(f"YouTube вернул {r.status_code}")
            return parse_feed(r.text, channel_id)


async def channel_url_for_video(video_id: str, proxy: str | None = None) -> str:
    async with httpx.AsyncClient(timeout=15, proxy=proxy, headers=UA) as c:
        r = await c.get("https://www.youtube.com/oembed",
                        params={"url": f"https://www.youtube.com/watch?v={video_id}", "format": "json"})
    if r.status_code != 200 or not r.json().get("author_url"):
        raise FeedError("Не удалось определить канал этого видео.")
    return r.json()["author_url"]
