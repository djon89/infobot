"""Инлайн-кнопки. callback_data ≤ 64 байт:
dl:<vid>:<q|d>:<a|d>   — скачать (d = по умолчанию)
pick:<vid>             — выбрать качество
q:<vid>:<q>            — выбрано качество → выбор дорожки или сразу скачивание
set:q:<q> / set:a:<a>  — настройки по умолчанию
"""
from aiogram.types import InlineKeyboardButton as B
from aiogram.types import InlineKeyboardMarkup

from app.services.downloader import AUDIO_DEFAULTS, QUALITY_LABELS, AudioTrack, human_size


def video_actions(video_id: str, quality: str, audio: str) -> InlineKeyboardMarkup:
    label = QUALITY_LABELS.get(quality, quality)
    if quality != "audio" and audio != "orig":
        label += f", {AUDIO_DEFAULTS.get(audio, audio)}"
    return InlineKeyboardMarkup(inline_keyboard=[[
        B(text=f"⬇️ Скачать ({label})", callback_data=f"dl:{video_id}:d:d"),
        B(text="🎚 Выбрать качество", callback_data=f"pick:{video_id}"),
    ]])


def quality_picker(video_id: str, sizes: dict[str, int], limit: int) -> InlineKeyboardMarkup:
    rows, row = [], []
    for q in ("360", "480", "720", "1080", "best", "audio"):
        if q not in sizes:
            continue
        size = sizes[q]
        text = QUALITY_LABELS[q] + (f" · ~{human_size(size)}" if size else "")
        if size > limit:
            text = "🚫 " + text
        row.append(B(text=text, callback_data=f"q:{video_id}:{q}"))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    return InlineKeyboardMarkup(inline_keyboard=rows)


def audio_picker(video_id: str, quality: str, tracks: list[AudioTrack]) -> InlineKeyboardMarkup:
    rows = [[B(text=("⭐ " if t.is_original else "") + f"{t.name} ({t.lang})",
               callback_data=f"dl:{video_id}:{quality}:{t.lang[:12]}")] for t in tracks[:12]]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def settings_kb(quality: str, audio: str) -> InlineKeyboardMarkup:
    def mark(cur, val, text):
        return ("✅ " if cur == val else "") + text
    qs = [B(text=mark(quality, q, QUALITY_LABELS[q]), callback_data=f"set:q:{q}") for q in QUALITY_LABELS]
    au = [B(text=mark(audio, a, t), callback_data=f"set:a:{a}") for a, t in AUDIO_DEFAULTS.items()]
    return InlineKeyboardMarkup(inline_keyboard=[qs[:3], qs[3:], au])


def settings_text(quality: str, audio: str) -> str:
    return ("⚙️ <b>Настройки скачивания по умолчанию</b>\n\n"
            f"Качество: <b>{QUALITY_LABELS.get(quality, quality)}</b>\n"
            f"Аудиодорожка: <b>{AUDIO_DEFAULTS.get(audio, audio)}</b>\n\n"
            "Их использует кнопка «⬇️ Скачать». Для разового выбора есть «🎚 Выбрать качество». "
            "Если нужной дорожки в видео нет, будет оригинальная.")
