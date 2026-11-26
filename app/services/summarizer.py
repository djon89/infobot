from app.services.llm.base import LLMProvider
from app.services.transcript.base import Transcript
from app.services.youtube import VideoMeta

SYSTEM = """Ты делаешь конспекты видео по их расшифровке. Пиши по-русски, \
даже если видео на другом языке. Расшифровка может быть автоматической и \
содержать ошибки распознавания — исправляй очевидные по смыслу, но ничего не выдумывай.

Формат ответа (Markdown, только эти элементы: **жирный**, списки, без заголовков #):
**О чём видео** — 2–3 предложения.

**Ключевые мысли**
- 5–12 пунктов, каждый — законченная мысль с конкретикой (цифры, имена, примеры из видео).

**Практические выводы** — 2–5 пунктов, только если в видео есть советы/инструкции; иначе пропусти раздел.

Пункты можно снабжать таймкодом [мм:сс] из расшифровки, если он есть рядом с мыслью."""


def build_prompt(meta: VideoMeta, t: Transcript) -> str:
    kind = "автоматические субтитры" if t.is_generated else "субтитры автора"
    return (
        f"Видео: {meta.title}\n"
        + (f"Канал: {meta.author}\n" if meta.author else "")
        + f"Язык расшифровки: {t.language} ({kind})\n\n"
        f"<transcript>\n{t.as_timestamped()}\n</transcript>"
    )


async def summarize(llm: LLMProvider, meta: VideoMeta, t: Transcript) -> str:
    return await llm.complete(SYSTEM, build_prompt(meta, t))
