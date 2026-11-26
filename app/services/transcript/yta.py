"""Субтитры через youtube-transcript-api (+ резидентный прокси против банов YouTube)."""
import asyncio

from youtube_transcript_api import (
    AgeRestricted,
    IpBlocked,
    NoTranscriptFound,
    RequestBlocked,
    TranscriptsDisabled,
    VideoUnavailable,
    YouTubeTranscriptApi,
)
from youtube_transcript_api.proxies import GenericProxyConfig, WebshareProxyConfig

from .base import Blocked, NoTranscript, Segment, Transcript, TranscriptError


def pick_transcript(transcripts: list, preferred: list[str]):
    """Выбор дорожки:
    1) ручные субтитры на языке оригинала (язык авто-субтитров = язык речи);
    2) ручные на предпочитаемом языке; 3) авто-субтитры; 4) что есть."""
    manual = [t for t in transcripts if not t.is_generated]
    generated = [t for t in transcripts if t.is_generated]
    original = generated[0].language_code if generated else None
    for t in manual:
        if original and t.language_code.split("-")[0] == original.split("-")[0]:
            return t
    for lang in preferred:
        for t in manual:
            if t.language_code.split("-")[0] == lang:
                return t
    if generated:
        return generated[0]
    if manual:
        return manual[0]
    return None


class YTATranscriptProvider:
    name = "youtube-transcript-api"

    def __init__(self, preferred_languages: list[str], webshare_user=None, webshare_pass=None, proxy_url=None):
        if webshare_user and webshare_pass:
            proxy = WebshareProxyConfig(proxy_username=webshare_user, proxy_password=webshare_pass)
        elif proxy_url:
            proxy = GenericProxyConfig(http_url=proxy_url, https_url=proxy_url)
        else:
            proxy = None
        self._preferred = preferred_languages
        self._proxy = proxy

    def _get_sync(self, video_id: str) -> Transcript:
        api = YouTubeTranscriptApi(proxy_config=self._proxy)
        try:
            tl = list(api.list(video_id))
        except (TranscriptsDisabled, NoTranscriptFound):
            raise NoTranscript("У этого видео нет субтитров.")
        except (IpBlocked, RequestBlocked):
            raise Blocked("YouTube заблокировал запрос (IP сервера).")
        except AgeRestricted:
            raise TranscriptError("Видео с возрастным ограничением — субтитры недоступны.")
        except VideoUnavailable:
            raise TranscriptError("Видео недоступно (удалено или приватное).")
        chosen = pick_transcript(tl, self._preferred)
        if chosen is None:
            raise NoTranscript("У этого видео нет субтитров.")
        fetched = chosen.fetch()
        segments = [Segment(s.start, s.text.replace("\n", " ").strip()) for s in fetched.snippets if s.text.strip()]
        return Transcript(video_id, chosen.language_code, chosen.is_generated, segments, self.name)

    async def get(self, video_id: str) -> Transcript:
        return await asyncio.to_thread(self._get_sync, video_id)
