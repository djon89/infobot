"""Рендер структурного конспекта:
- rich-блоки Telegram (заголовки, таблицы, цитаты, таймкоды-ссылки) — основной вид;
- HTML — запасной вид, если rich-сообщение не принято;
- Markdown — файл для сохранения.
"""
import html

from aiogram.types import (
    InputRichBlockBlockQuotation,
    InputRichBlockDivider,
    InputRichBlockFooter,
    InputRichBlockList,
    InputRichBlockListItem,
    InputRichBlockParagraph,
    InputRichBlockPullQuotation,
    InputRichBlockSectionHeading,
    InputRichBlockTable,
    RichBlockTableCell,
    RichTextBold,
    RichTextItalic,
    RichTextUrl,
)

from app.services.youtube import VideoMeta


def ts(sec: int | None) -> str:
    if sec is None:
        return ""
    h, m, s = sec // 3600, sec % 3600 // 60, sec % 60
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def ts_url(video_id: str, sec: int) -> str:
    return f"https://youtu.be/{video_id}?t={sec}"


# ---------------- rich-блоки ----------------

def _p(*parts):
    return InputRichBlockParagraph(type="paragraph", text=list(parts) if len(parts) > 1 else parts[0])


def _h(text: str, size: int):
    return InputRichBlockSectionHeading(type="heading", text=text, size=size)


def _tlink(vid: str, sec: int | None):
    return [RichTextUrl(type="url", text=f"▶ {ts(sec)}", url=ts_url(vid, sec)), "  "] if sec is not None else []


def _list(items):
    """items: список RichText (строка или список частей)."""
    return InputRichBlockList(type="list", items=[
        InputRichBlockListItem(blocks=[InputRichBlockParagraph(type="paragraph", text=it)]) for it in items])


def _points(vid: str, pts) -> InputRichBlockList:
    return _list([[*_tlink(vid, p.get("t")), p["text"]] for p in pts])


def _table(tbl: dict) -> InputRichBlockTable:
    cols = len(tbl["columns"])
    head = [RichBlockTableCell(text=RichTextBold(type="bold", text=c), is_header=True, align="left", valign="top")
            for c in tbl["columns"]]
    rows = [[RichBlockTableCell(text=str(r[i]) if i < len(r) else "", align="left", valign="top")
             for i in range(cols)] for r in tbl["rows"]]
    return InputRichBlockTable(type="table", cells=[head, *rows], is_bordered=True, is_striped=True,
                               caption=tbl.get("caption") or None)


def _header_blocks(meta: VideoMeta, d: dict, title_prefix: str = ""):
    by = [RichTextUrl(type="url", text=meta.title, url=meta.url)]
    if meta.author:
        by += [" · ", meta.author]
    return [_h(title_prefix + d["headline"], 1), _p(RichTextItalic(type="italic", text=by))]


def summary_blocks(meta: VideoMeta, d: dict, transcript_note: str = "") -> list:
    vid = meta.video_id
    b = _header_blocks(meta, d)
    b.append(InputRichBlockBlockQuotation(type="blockquote", blocks=[_p(d["tldr"])]))

    b += [_h("🔑 Главное", 2), _points(vid, d["key_points"])]

    b.append(_h("🧭 По частям", 2))
    for i, s in enumerate(d["sections"], 1):
        b.append(_h(f"{i}. {s['title']}", 3))
        b.append(_p(RichTextUrl(type="url", text=f"▶ {ts(s['start'])}–{ts(s['end'])}", url=ts_url(vid, s["start"]))))
        b.append(_p(s["summary"]))
        if s.get("points"):
            b.append(_points(vid, s["points"]))

    if d.get("table"):
        b += [_h("📊 " + (d["table"].get("caption") or "Сводная таблица"), 2), _table(d["table"])]

    if d.get("quotes"):
        b.append(_h("💬 Цитаты", 2))
        for q in d["quotes"]:
            credit = RichTextUrl(type="url", text=f"▶ {ts(q['t'])}", url=ts_url(vid, q["t"])) if q.get("t") is not None else None
            b.append(InputRichBlockPullQuotation(type="pullquote", text=f"«{q['text']}»", credit=credit))

    if d.get("takeaways"):
        b += [_h("✅ Что применить", 2), _list(d["takeaways"])]
    if d.get("critique"):
        b += [_h("⚠️ Критический взгляд", 2), _list(d["critique"])]

    b += [_h("🎯 Вывод", 2), _p(d["conclusion"])]
    if d.get("watch_advice"):
        b.append(_p(RichTextBold(type="bold", text="Смотреть ли целиком: "), d["watch_advice"]))
    b.append(InputRichBlockDivider(type="divider"))
    b.append(InputRichBlockFooter(type="footer", text=f"Конспект по расшифровке{transcript_note}. Таймкоды ▶ открывают видео на нужном месте."))
    return b


def short_blocks(meta: VideoMeta, d: dict) -> list:
    b = _header_blocks(meta, d, "⚡ ")
    b.append(_p(d["tldr"]))
    b.append(_points(meta.video_id, d["key_points"][:3]))
    if d.get("watch_advice"):
        b.append(_p(RichTextBold(type="bold", text="Смотреть ли: "), d["watch_advice"]))
    return b


def deep_blocks(meta: VideoMeta, d: dict, sec: dict, idx: int, deep: dict) -> list:
    vid = meta.video_id
    b = [_h(f"🔍 Часть {idx + 1}. {sec['title']}", 1),
         _p(RichTextItalic(type="italic", text=[RichTextUrl(type="url", text=meta.title, url=meta.url),
                                                f" · {ts(sec['start'])}–{ts(sec['end'])}"])),
         InputRichBlockBlockQuotation(type="blockquote", blocks=[_p(deep["intro"])])]
    for blk in deep["blocks"]:
        b.append(_h(blk["title"], 2))
        if blk.get("t") is not None:
            b.append(_p(RichTextUrl(type="url", text=f"▶ {ts(blk['t'])}", url=ts_url(vid, blk["t"]))))
        b += [_p(p) for p in blk.get("paragraphs") or []]
        if blk.get("points"):
            b.append(_list(blk["points"]))
    if deep.get("terms"):
        b += [_h("📖 Термины", 2),
              _list([[RichTextBold(type="bold", text=t["term"]), " — ", t["meaning"]] for t in deep["terms"]])]
    b += [_h("🎯 Итог части", 2), _p(deep["bottom_line"])]
    return b


# ---------------- HTML (запасной вид) ----------------

def _e(t: str) -> str:
    return html.escape(t, quote=False)


def _hl(vid: str, sec: int | None) -> str:
    return f'<a href="{ts_url(vid, sec)}">▶ {ts(sec)}</a>  ' if sec is not None else ""


def summary_html(meta: VideoMeta, d: dict) -> str:
    vid = meta.video_id
    out = [f"<b>📝 {_e(d['headline'])}</b>", f'<i><a href="{meta.url}">{_e(meta.title)}</a></i>', "",
           f"<blockquote>{_e(d['tldr'])}</blockquote>", "", "<b>🔑 Главное</b>"]
    out += [f"• {_hl(vid, p.get('t'))}{_e(p['text'])}" for p in d["key_points"]]
    out += ["", "<b>🧭 По частям</b>"]
    for i, s in enumerate(d["sections"], 1):
        out += ["", f"<b>{i}. {_e(s['title'])}</b>  {_hl(vid, s['start']).strip()}", _e(s["summary"])]
        out += [f"  • {_hl(vid, p.get('t'))}{_e(p['text'])}" for p in s.get("points") or []]
    if d.get("table"):
        t = d["table"]
        out += ["", f"<b>📊 {_e(t.get('caption') or 'Таблица')}</b>"]
        out += [f"• {_e(' | '.join(map(str, r)))}" for r in [t["columns"], *t["rows"]]]
    for title, key in (("✅ Что применить", "takeaways"), ("⚠️ Критический взгляд", "critique")):
        if d.get(key):
            out += ["", f"<b>{title}</b>"] + [f"• {_e(x)}" for x in d[key]]
    out += ["", "<b>🎯 Вывод</b>", _e(d["conclusion"])]
    if d.get("watch_advice"):
        out += ["", f"<b>Смотреть ли целиком:</b> {_e(d['watch_advice'])}"]
    return "\n".join(out)


def short_html(meta: VideoMeta, d: dict) -> str:
    vid = meta.video_id
    out = [f"<b>⚡ {_e(d['headline'])}</b>", "", _e(d["tldr"]), ""]
    out += [f"• {_hl(vid, p.get('t'))}{_e(p['text'])}" for p in d["key_points"][:3]]
    return "\n".join(out)


def deep_html(meta: VideoMeta, sec: dict, idx: int, deep: dict) -> str:
    vid = meta.video_id
    out = [f"<b>🔍 Часть {idx + 1}. {_e(sec['title'])}</b>", f"<i>{_e(meta.title)}</i>", "", _e(deep["intro"])]
    for blk in deep["blocks"]:
        out += ["", f"<b>{_e(blk['title'])}</b>  {_hl(vid, blk.get('t')).strip()}"]
        out += [_e(p) for p in blk.get("paragraphs") or []]
        out += [f"• {_e(x)}" for x in blk.get("points") or []]
    if deep.get("terms"):
        out += ["", "<b>📖 Термины</b>"] + [f"• <b>{_e(t['term'])}</b> — {_e(t['meaning'])}" for t in deep["terms"]]
    out += ["", f"<b>🎯 Итог:</b> {_e(deep['bottom_line'])}"]
    return "\n".join(out)


# ---------------- Markdown (файл) ----------------

def summary_markdown(meta: VideoMeta, d: dict) -> str:
    vid = meta.video_id

    def tl(sec):
        return f"[{ts(sec)}]({ts_url(vid, sec)}) " if sec is not None else ""
    out = [f"# {d['headline']}", "", f"*[{meta.title}]({meta.url})" + (f" · {meta.author}*" if meta.author else "*"),
           "", f"> {d['tldr']}", "", "## 🔑 Главное", ""]
    out += [f"- {tl(p.get('t'))}{p['text']}" for p in d["key_points"]]
    out += ["", "## 🧭 По частям"]
    for i, s in enumerate(d["sections"], 1):
        out += ["", f"### {i}. {s['title']} — {tl(s['start']).strip()}", "", s["summary"], ""]
        out += [f"- {tl(p.get('t'))}{p['text']}" for p in s.get("points") or []]
    if d.get("table"):
        t = d["table"]
        out += ["", f"## 📊 {t.get('caption') or 'Таблица'}", "", "| " + " | ".join(t["columns"]) + " |",
                "|" + "---|" * len(t["columns"])]
        out += ["| " + " | ".join(map(str, r)) + " |" for r in t["rows"]]
    if d.get("quotes"):
        out += ["", "## 💬 Цитаты", ""] + [f"> «{q['text']}» {tl(q.get('t'))}\n" for q in d["quotes"]]
    for title, key in (("✅ Что применить", "takeaways"), ("⚠️ Критический взгляд", "critique")):
        if d.get(key):
            out += ["", f"## {title}", ""] + [f"- {x}" for x in d[key]]
    out += ["", "## 🎯 Вывод", "", d["conclusion"]]
    if d.get("watch_advice"):
        out += ["", f"**Смотреть ли целиком:** {d['watch_advice']}"]
    return "\n".join(out) + "\n"
