"""Конспект видео: промпт, JSON-схема ответа и разбор отдельной части видео подробно.

Модель возвращает структуру (а не готовый текст), поэтому одно и то же содержимое
рендерится в «статью» Telegram, в короткую версию, в .md-файл и в кнопки по разделам.
"""
import json

from app.services.llm.base import LLMError, LLMProvider
from app.services.transcript.base import Transcript
from app.services.youtube import VideoMeta

SCHEMA_VERSION = 2

_POINT = {
    "type": "object",
    "properties": {
        "text": {"type": "string"},
        "t": {"type": ["integer", "null"], "description": "секунда видео, где это сказано; null, если неясно"},
    },
    "required": ["text", "t"],
    "additionalProperties": False,
}

SUMMARY_SCHEMA = {
    "type": "object",
    "properties": {
        "headline": {"type": "string"},
        "tldr": {"type": "string"},
        "key_points": {"type": "array", "items": _POINT},
        "sections": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "start": {"type": "integer"},
                    "end": {"type": "integer"},
                    "summary": {"type": "string"},
                    "points": {"type": "array", "items": _POINT},
                },
                "required": ["title", "start", "end", "summary", "points"],
                "additionalProperties": False,
            },
        },
        "table": {
            "anyOf": [
                {"type": "null"},
                {
                    "type": "object",
                    "properties": {
                        "caption": {"type": "string"},
                        "columns": {"type": "array", "items": {"type": "string"}},
                        "rows": {"type": "array", "items": {"type": "array", "items": {"type": "string"}}},
                    },
                    "required": ["caption", "columns", "rows"],
                    "additionalProperties": False,
                },
            ]
        },
        "quotes": {"type": "array", "items": _POINT},
        "takeaways": {"type": "array", "items": {"type": "string"}},
        "critique": {"type": "array", "items": {"type": "string"}},
        "conclusion": {"type": "string"},
        "watch_advice": {"type": "string"},
    },
    "required": ["headline", "tldr", "key_points", "sections", "table", "quotes",
                 "takeaways", "critique", "conclusion", "watch_advice"],
    "additionalProperties": False,
}

SYSTEM = """Ты — редактор, который превращает расшифровку видео в конспект-статью. \
Читатель хочет за 3–5 минут получить всё ценное из видео и понять, стоит ли смотреть его целиком.

Язык: всегда русский, даже если видео на другом языке. Имена, названия продуктов и термины \
оставляй как в оригинале, при первом упоминании поясняй в скобках.

Источник — расшифровка с таймкодами [мм:сс] или [ч:мм:сс]. Она может быть автоматической: \
исправляй очевидные ошибки распознавания по смыслу, но ничего не выдумывай. Если в видео чего-то нет — \
не дописывай от себя. Факты, цифры, имена, примеры, рецепты, шаги бери из видео максимально конкретно: \
«рост на 30% за 2024 год» лучше, чем «значительный рост».

Поля ответа:
• headline — суть видео одной фразой до 90 символов. Не копия названия ролика, а его главная мысль \
или ответ на вопрос, который ставит видео (кликбейтное название — расшифровывай, что на самом деле внутри).
• tldr — 2–3 предложения: о чём видео и к какому выводу приходит автор.
• key_points — 3–7 главных тезисов всего видео, от самого важного. Каждый — законченная мысль \
в 1–2 предложения с конкретикой, понятная без контекста. Поле t — секунда, где тезис звучит.
• sections — смысловые части видео по порядку, без пропусков от начала до конца: 3–10 частей \
(для коротких видео меньше, для лекций больше). start/end — секунды границ части. title — 2–6 слов, \
по сути, а не «Введение/Часть 2». summary — абзац 2–4 предложения о том, что в этой части говорится \
и почему это важно. points — 2–5 конкретных деталей части (аргументы, примеры, цифры, шаги) с t.
• table — только если в видео есть то, что естественно ложится в таблицу: сравнение вариантов, \
характеристики, цены, цифры по годам, плюсы/минусы, пошаговый план. 2–5 колонок, до 12 строк, ячейки короткие. \
Иначе null — не выдумывай таблицу ради таблицы.
• quotes — 0–3 самые яркие или точные формулировки спикера (в переводе на русский, если видео не на русском), с t. \
Только реальные высказывания из расшифровки.
• takeaways — 0–5 практических выводов: что читатель может сделать или применить. Пустой список, \
если видео не про практику (новости, история, развлечение).
• critique — 0–3 пункта трезвого взгляда: спорные или не подкреплённые утверждения, реклама \
и интеграции, конфликт интересов, важное, о чём умолчали. Пустой список, если сказать нечего; не придирайся.
• conclusion — итог в 2–4 предложения: главный вывод и его значение для читателя.
• watch_advice — одно предложение: стоит ли смотреть целиком, кому, или какие отрезки посмотреть (с [мм:сс]).

Тон — нейтральный, плотный, без воды и вводных фраз вроде «в этом видео автор рассказывает». \
Не используй Markdown-разметку внутри полей: это обычный текст."""


def build_prompt(meta: VideoMeta, t: Transcript) -> str:
    kind = "автоматические субтитры" if t.is_generated else "субтитры автора"
    duration = int(t.segments[-1].start) if t.segments else 0
    return (
        f"Видео: {meta.title}\n"
        + (f"Канал: {meta.author}\n" if meta.author else "")
        + f"Длительность: ~{duration // 60} мин ({duration} с)\n"
        f"Язык расшифровки: {t.language} ({kind})\n\n"
        f"<transcript>\n{t.as_timestamped()}\n</transcript>"
    )


def _clean(d: dict, duration: int) -> dict:
    """Подстраховка от мусора: таймкоды в пределах видео, разделы по порядку."""
    def ok_t(v):
        return v if isinstance(v, int) and 0 <= v <= max(duration, 1) + 5 else None
    for group in ("key_points", "quotes"):
        for p in d.get(group) or []:
            p["t"] = ok_t(p.get("t"))
    secs = [s for s in d.get("sections") or [] if isinstance(s.get("start"), int)]
    secs.sort(key=lambda s: s["start"])
    for s in secs:
        s["start"] = max(0, min(s["start"], duration))
        s["end"] = max(s["start"], min(s.get("end") or duration, duration or s["start"]))
        for p in s.get("points") or []:
            p["t"] = ok_t(p.get("t"))
    d["sections"] = secs
    tbl = d.get("table")
    if tbl and (not tbl.get("columns") or not tbl.get("rows")):
        d["table"] = None
    d["v"] = SCHEMA_VERSION
    return d


async def summarize(llm: LLMProvider, meta: VideoMeta, t: Transcript) -> dict:
    duration = int(t.segments[-1].start) if t.segments else 0
    d = await llm.complete_json(SYSTEM, build_prompt(meta, t), SUMMARY_SCHEMA)
    if not d.get("key_points") or not d.get("sections"):
        raise LLMError("Модель вернула пустой конспект.")
    return _clean(d, duration)


def load_summary(raw: str | None) -> dict | None:
    """Конспект из БД. Старые текстовые конспекты (до структурного формата) — None, чтобы пересоздать."""
    if not raw or not raw.lstrip().startswith("{"):
        return None
    try:
        d = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return d if d.get("v") == SCHEMA_VERSION else None


# ---------- подробный разбор одной части ----------

DEEP_SCHEMA = {
    "type": "object",
    "properties": {
        "intro": {"type": "string"},
        "blocks": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "t": {"type": ["integer", "null"]},
                    "paragraphs": {"type": "array", "items": {"type": "string"}},
                    "points": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["title", "t", "paragraphs", "points"],
                "additionalProperties": False,
            },
        },
        "terms": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"term": {"type": "string"}, "meaning": {"type": "string"}},
                "required": ["term", "meaning"],
                "additionalProperties": False,
            },
        },
        "bottom_line": {"type": "string"},
    },
    "required": ["intro", "blocks", "terms", "bottom_line"],
    "additionalProperties": False,
}

DEEP_SYSTEM = """Ты подробно разбираешь один фрагмент видео по его расшифровке — так, чтобы \
смотреть этот фрагмент стало не нужно. Пиши по-русски, плотно и конкретно, без воды.

Сохрани всю логику рассуждения спикера: аргументы, примеры, цифры, шаги, оговорки, \
ответы на возражения. Ничего не выдумывай — только то, что есть в расшифровке; очевидные ошибки \
автоматических субтитров исправляй по смыслу.

Поля ответа:
• intro — 1–2 предложения: о чём этот фрагмент и как он связан с видео в целом.
• blocks — 2–6 последовательных смысловых блоков фрагмента. title — 2–6 слов; t — секунда начала блока; \
paragraphs — 1–3 абзаца связного объяснения; points — 0–6 конкретных деталей (цифры, шаги, примеры).
• terms — 0–6 терминов или имён из фрагмента, которые стоит пояснить читателю.
• bottom_line — главный вывод фрагмента в 1–2 предложения.
Не используй Markdown-разметку внутри полей."""


def deep_dive_prompt(meta: VideoMeta, summary: dict, idx: int, t: Transcript) -> str:
    sec = summary["sections"][idx]
    start, end = sec["start"], sec["end"]
    pad = 20
    seg = [s for s in t.segments if start - pad <= s.start <= end + pad]
    part = Transcript(t.video_id, t.language, t.is_generated, seg, t.source)
    outline = "\n".join(f"{i + 1}. {s['title']}" for i, s in enumerate(summary["sections"]))
    return (
        f"Видео: {meta.title}\nГлавная мысль видео: {summary.get('headline', '')}\n"
        f"План видео:\n{outline}\n\n"
        f"Разбери подробно часть {idx + 1} «{sec['title']}» ({start}–{end} с).\n\n"
        f"<transcript>\n{part.as_timestamped()}\n</transcript>"
    )


async def deep_dive(llm: LLMProvider, meta: VideoMeta, summary: dict, idx: int, t: Transcript) -> dict:
    d = await llm.complete_json(DEEP_SYSTEM, deep_dive_prompt(meta, summary, idx, t), DEEP_SCHEMA)
    if not d.get("blocks"):
        raise LLMError("Модель вернула пустой разбор.")
    return d
