"""Сводка новых видео: одно сообщение со страницами и кнопками вместо сотни сообщений."""
import html
from datetime import datetime, timezone

from aiogram.types import InlineKeyboardButton as B
from aiogram.types import InlineKeyboardMarkup

PAGE = 8


def ago(dt: datetime, now: datetime | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    dt = dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    m = int((now - dt).total_seconds() // 60)
    if m < 60:
        return f"{max(m, 1)} мин назад"
    if m < 60 * 24:
        return f"{m // 60} ч назад"
    d = m // (60 * 24)
    return "вчера" if d == 1 else f"{d} дн назад"


def render(items, page: int, digest_id: int, title: str = "📬 Новые видео", now=None,
           blurbs: dict[str, str] | None = None):
    """items: [(FeedVideo, Channel)], blurbs: video_id → строка «что внутри». Возвращает (text, keyboard)."""
    blurbs = blurbs or {}
    pages = max(1, (len(items) + PAGE - 1) // PAGE)
    page = min(max(page, 0), pages - 1)
    chunk = items[page * PAGE:(page + 1) * PAGE]
    head = f"<b>{title}</b> · {len(items)} шт."
    if pages > 1:
        head += f" · стр. {page + 1}/{pages}"
    lines = [head, ""]
    rows, row = [], []
    for i, (v, c) in enumerate(chunk, start=page * PAGE + 1):
        url = f"https://www.youtube.com/watch?v={v.video_id}"
        line = f"<b>{i}.</b> <a href=\"{url}\">{html.escape(v.title)}</a>\n"
        if blurbs.get(v.video_id):
            line += f"     ↳ {html.escape(blurbs[v.video_id])}\n"
        line += f"     <i>{html.escape(c.title)}</i> · {ago(v.published_at, now)}"
        lines.append(line)
        row += [B(text=f"📝 {i}", callback_data=f"s:{v.video_id}"),
                B(text=f"⬇️ {i}", callback_data=f"dl:{v.video_id}:d:d")]
        if len(row) == 4:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    if pages > 1:
        rows.append([
            B(text="◀️", callback_data=f"dg:{digest_id}:{page - 1}" if page > 0 else "noop"),
            B(text=f"{page + 1}/{pages}", callback_data="noop"),
            B(text="▶️", callback_data=f"dg:{digest_id}:{page + 1}" if page < pages - 1 else "noop"),
        ])
    lines.append("\n📝 — конспект, ⬇️ — скачать")
    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows)


CH_PAGE = 10


def render_channels(channels, page: int):
    pages = max(1, (len(channels) + CH_PAGE - 1) // CH_PAGE)
    page = min(max(page, 0), pages - 1)
    chunk = channels[page * CH_PAGE:(page + 1) * CH_PAGE]
    if not channels:
        return ("Вы пока ни за кем не следите.\n\nПришлите ссылку на канал (например, "
                "<code>youtube.com/@channel</code>) или нажмите «➕ Следить за каналом» под конспектом. "
                "Импорт подписок YouTube — /import."), None
    text = f"<b>📺 Каналы, за которыми вы следите</b> · {len(channels)}"
    if pages > 1:
        text += f" · стр. {page + 1}/{pages}"
    text += "\n\n📺 — последние видео канала, ❌ — отписаться."
    rows = [[B(text=f"📺 {c.title[:40]}", callback_data=f"chv:{c.id}"),
             B(text="❌", callback_data=f"uf:{c.id}:{page}")] for c in chunk]
    if pages > 1:
        rows.append([B(text="◀️", callback_data=f"chp:{page - 1}" if page > 0 else "noop"),
                     B(text=f"{page + 1}/{pages}", callback_data="noop"),
                     B(text="▶️", callback_data=f"chp:{page + 1}" if page < pages - 1 else "noop")])
    return text, InlineKeyboardMarkup(inline_keyboard=rows)
