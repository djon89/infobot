from dataclasses import dataclass
from typing import Protocol


@dataclass
class Segment:
    start: float
    text: str


@dataclass
class Transcript:
    video_id: str
    language: str
    is_generated: bool
    segments: list[Segment]
    source: str

    @property
    def plain_text(self) -> str:
        return " ".join(s.text for s in self.segments)

    def as_timestamped(self) -> str:
        lines = []
        for s in self.segments:
            t = int(s.start)
            h, m, sec = t // 3600, t % 3600 // 60, t % 60
            ts = f"{h}:{m:02d}:{sec:02d}" if h else f"{m:02d}:{sec:02d}"
            lines.append(f"[{ts}] {s.text}")
        return "\n".join(lines)


class TranscriptError(Exception):
    """Ошибка, которую можно показать пользователю."""


class NoTranscript(TranscriptError):
    pass


class Blocked(TranscriptError):
    pass


class TranscriptProvider(Protocol):
    name: str

    async def get(self, video_id: str) -> Transcript: ...
