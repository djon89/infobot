"""Безопасный перевод простого Markdown от модели в Telegram-HTML и нарезка сообщений."""
import html
import re

TG_LIMIT = 4096


def md_to_tg_html(md: str) -> str:
    out = []
    for line in md.splitlines():
        line = html.escape(line, quote=False)
        line = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", line)
        line = re.sub(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])", r"<i>\1</i>", line)
        line = re.sub(r"`([^`]+)`", r"<code>\1</code>", line)
        line = re.sub(r"^#{1,6}\s+(.*)$", r"<b>\1</b>", line)
        line = re.sub(r"^(\s*)[-*]\s+", r"\1• ", line)
        out.append(line)
    return "\n".join(out)


def split_message(text: str, limit: int = TG_LIMIT) -> list[str]:
    """Режет по абзацам/строкам; HTML-теги в нашем формате не переносятся через строку."""
    if len(text) <= limit:
        return [text]
    parts, cur = [], ""
    for line in text.split("\n"):
        while len(line) > limit:  # очень длинная строка
            if cur:
                parts.append(cur)
                cur = ""
            parts.append(line[:limit])
            line = line[limit:]
        candidate = f"{cur}\n{line}" if cur else line
        if len(candidate) > limit:
            parts.append(cur)
            cur = line
        else:
            cur = candidate
    if cur:
        parts.append(cur)
    return parts


def safe_filename(title: str, suffix: str, max_len: int = 60) -> str:
    name = re.sub(r"[^\w\s\-.,()]+", "", title, flags=re.UNICODE).strip()
    name = re.sub(r"\s+", " ", name)[:max_len].strip() or "video"
    return f"{name} — {suffix}"
