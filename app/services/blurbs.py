"""Умная сводка: одна строка о каждом видео по названию и описанию — одним запросом на всю пачку."""
from app.services.llm.base import LLMProvider

SCHEMA = {
    "type": "object",
    "properties": {"items": {"type": "array", "items": {
        "type": "object",
        "properties": {"id": {"type": "string"}, "line": {"type": "string"}},
        "required": ["id", "line"], "additionalProperties": False}}},
    "required": ["items"], "additionalProperties": False,
}

SYSTEM = """Ты помогаешь за 10 секунд понять, какие из новых видео стоит открыть.
Для каждого видео напиши по-русски одну строку до 110 символов: что внутри по сути — \
тема, главный вопрос или вывод, формат (интервью, обзор, туториал, новости).
Если название кликбейтное — скажи, о чём видео на самом деле. Не пересказывай название другими словами, \
не используй «В этом видео…». Опирайся только на название и описание; если описание пустое или это \
только ссылки и реклама — пиши осторожно, по названию. Вернуть строку нужно для каждого id."""


def build_prompt(items: list[dict]) -> str:
    parts = []
    for it in items:
        desc = (it.get("description") or "").strip().replace("\n", " ")[:600]
        parts.append(f"<video id=\"{it['id']}\">\nНазвание: {it['title']}\nКанал: {it['channel']}\n"
                     f"Описание: {desc or '—'}\n</video>")
    return "\n\n".join(parts)


async def make_blurbs(llm: LLMProvider, items: list[dict]) -> dict[str, str]:
    if not items:
        return {}
    d = await llm.complete_json(SYSTEM, build_prompt(items), SCHEMA)
    ids = {it["id"] for it in items}
    return {x["id"]: x["line"].strip()[:160] for x in d.get("items", []) if x.get("id") in ids and x.get("line")}
