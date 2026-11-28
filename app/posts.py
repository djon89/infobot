"""Посты со списком видео: карточки с обложкой, строкой «что внутри», кнопками и командами
/sum_<пост>_<номер> и /dl_<пост>_<номер> — чтобы не листать длинный пост к кнопкам."""
import html
from datetime import datetime, timezone

from aiogram.types import InlineKeyboardButton as B
from aiogram.types import (
    InlineKeyboardMarkup,
    InputMediaPhoto,
    InputRichBlockDivider,
    InputRichBlockFooter,
    InputRichBlockParagraph,
    InputRichBlockPhoto,
    InputRichBlockSectionHeading,
    RichTextBold,
    RichTextItalic,
    RichTextUrl,
)

from app.digest import ago

DIGEST_PAGE = 6
CHANNEL_PAGE = 5


def item(video_id: str, title: str, channel: str, published: datetime | None = None,
         duration: int | None = None, blurb: str | None = None) -> dict:
    return {"id": video_id, "title": title, "channel": channel,
            "published": published.isoformat() if published else None,
            "duration": duration, "blurb": blurb}


def cover_url(video_id: str) -> str:
    # mqdefault — 16:9 без чёрных полос и есть у любого видео
    return f"https://i.ytimg.com/vi/{video_id}/mqdefault.jpg"


def _dur(sec: int | None) -> str:
    if not sec:
        return ""
    h, m, s = sec // 3600, sec % 3600 // 60, sec % 60
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def _meta_line(it: dict, show_channel: bool) -> str:
    parts = []
    if show_channel:
        parts.append(it["channel"])
    if it.get("published"):
        parts.append(ago(datetime.fromisoformat(it["published"])))
    if it.get("duration"):
        parts.append("⏱ " + _dur(it["duration"]))
    return " · ".join(parts)


def _p(text):
    return InputRichBlockParagraph(type="paragraph", text=text)


def page_slice(post, page: int) -> tuple[int, int, int]:
    """(start, end, pages) — для сводки листаем внутри поста; страница канала — один пост."""
    if post.kind == "channel":
        return 0, len(post.items), 1
    pages = max(1, (len(post.items) + DIGEST_PAGE - 1) // DIGEST_PAGE)
    page = min(max(page, 0), pages - 1)
    return page * DIGEST_PAGE, min(len(post.items), (page + 1) * DIGEST_PAGE), pages


def header(post, page: int, pages: int) -> tuple[str, str | None]:
    m = post.meta or {}
    if post.kind == "channel":
        title = f"📺 {m.get('channel_title', 'Канал')}"
        sub = f"Видео {m.get('offset', 0) + 1}–{m.get('offset', 0) + len(post.items)}"
        if m.get("followed"):
            sub += " · ✅ вы следите"
        return title, sub
    title = m.get("title") or "📬 Новые видео"
    sub = f"{len(post.items)} шт." + (f" · стр. {page + 1}/{pages}" if pages > 1 else "")
    return title, sub


def blocks(post, page: int = 0) -> list:
    start, end, pages = page_slice(post, page)
    title, sub = header(post, page, pages)
    b = [InputRichBlockSectionHeading(type="heading", text=title, size=1)]
    if sub:
        b.append(_p(RichTextItalic(type="italic", text=sub)))
    desc = (post.meta or {}).get("channel_description")
    if post.kind == "channel" and desc and not (post.meta or {}).get("offset"):
        b.append(_p(desc[:300] + ("…" if len(desc) > 300 else "")))
    show_channel = post.kind != "channel"
    for i in range(start, end):
        it, n = post.items[i], i + 1
        url = f"https://www.youtube.com/watch?v={it['id']}"
        b.append(InputRichBlockDivider(type="divider"))
        b.append(InputRichBlockPhoto(type="photo", photo=InputMediaPhoto(media=it.get("cover") or cover_url(it["id"]))))
        b.append(InputRichBlockSectionHeading(type="heading", size=3,
                                              text=RichTextUrl(type="url", text=f"{n}. {it['title']}", url=url)))
        if it.get("blurb"):
            b.append(_p(it["blurb"]))
        meta = _meta_line(it, show_channel)
        if meta:
            b.append(_p(RichTextItalic(type="italic", text=meta)))
        b.append(_p([RichTextBold(type="bold", text="📝 "), f"/sum_{post.id}_{n}", "   ",
                     RichTextBold(type="bold", text="⬇️ "), f"/dl_{post.id}_{n}"]))
    b.append(InputRichBlockDivider(type="divider"))
    b.append(InputRichBlockFooter(type="footer", text="📝 — конспект, ⬇️ — скачать. Нажмите команду под видео или кнопку внизу."))
    return b


def as_html(post, page: int = 0) -> str:
    """Запасной вид, если rich-сообщение не принято."""
    start, end, pages = page_slice(post, page)
    title, sub = header(post, page, pages)
    out = [f"<b>{html.escape(title)}</b>" + (f" · {html.escape(sub)}" if sub else ""), ""]
    for i in range(start, end):
        it, n = post.items[i], i + 1
        out.append(f'<b>{n}.</b> <a href="https://www.youtube.com/watch?v={it["id"]}">{html.escape(it["title"])}</a>')
        if it.get("blurb"):
            out.append(f"     ↳ {html.escape(it['blurb'])}")
        meta = _meta_line(it, post.kind != "channel")
        if meta:
            out.append(f"     <i>{html.escape(meta)}</i>")
        out.append(f"     📝 /sum_{post.id}_{n}   ⬇️ /dl_{post.id}_{n}")
        out.append("")
    return "\n".join(out).strip()


def keyboard(post, page: int = 0) -> InlineKeyboardMarkup:
    start, end, pages = page_slice(post, page)
    rows, row = [], []
    for i in range(start, end):
        n, vid = i + 1, post.items[i]["id"]
        row += [B(text=f"📝 {n}", callback_data=f"s:{vid}"), B(text=f"⬇️ {n}", callback_data=f"dl:{vid}:d:d")]
        if len(row) == 4:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    m = post.meta or {}
    if post.kind == "channel":
        cid, off = m.get("channel_id"), m.get("offset", 0)
        nav = []
        if off > 0:
            nav.append(B(text="◀️ Новее", callback_data=f"chb:{cid}:{max(0, off - CHANNEL_PAGE)}:{post.id}"))
        if m.get("has_more", True):
            nav.append(B(text=f"Ещё {CHANNEL_PAGE} видео ▶️", callback_data=f"chb:{cid}:{off + CHANNEL_PAGE}:{post.id}"))
        if nav:
            rows.append(nav)
        if not m.get("followed"):
            rows.append([B(text="🔔 Следить за каналом", callback_data=f"fc:{cid}:{post.id}")])
    elif pages > 1:
        rows.append([
            B(text="◀️", callback_data=f"pg:{post.id}:{page - 1}" if page > 0 else "noop"),
            B(text=f"{page + 1}/{pages}", callback_data="noop"),
            B(text="▶️", callback_data=f"pg:{post.id}:{page + 1}" if page < pages - 1 else "noop"),
        ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def parse_command(text: str) -> tuple[str, int, int] | None:
    """'/sum_12_3' или '/dl_12_3@bot' → ('sum', 12, 3)."""
    import re
    m = re.match(r"^/(sum|dl)_(\d+)_(\d+)(?:@\w+)?\b", text or "")
    return (m.group(1), int(m.group(2)), int(m.group(3))) if m else None


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
