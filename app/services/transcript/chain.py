import logging

from .base import Blocked, NoTranscript, Transcript, TranscriptError, TranscriptProvider

log = logging.getLogger(__name__)


class TranscriptChain:
    """Пробует провайдеров по очереди. Сюда же позже встанут yt-dlp и Whisper (видео без субтитров)."""

    def __init__(self, providers: list[TranscriptProvider]):
        self.providers = providers

    async def get(self, video_id: str) -> Transcript:
        last: Exception | None = None
        for p in self.providers:
            try:
                return await p.get(video_id)
            except NoTranscript as e:
                last = e  # у следующего провайдера (например, Whisper) может получиться
            except Blocked as e:
                log.warning("provider %s blocked for %s", p.name, video_id)
                last = e
            except TranscriptError:
                raise
            except Exception as e:  # noqa: BLE001
                log.exception("provider %s failed for %s", p.name, video_id)
                last = e
        if isinstance(last, TranscriptError):
            raise last
        raise TranscriptError("Не удалось получить расшифровку.") from last
