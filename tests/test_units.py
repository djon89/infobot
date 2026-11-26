import json
import os
import stat
from types import SimpleNamespace as NS

import pytest

from app.services.llm.claude_cli import ClaudeCliProvider
from app.services.llm.base import LLMError
from app.services.transcript.base import Segment, Transcript
from app.services.transcript.yta import pick_transcript
from app.services.youtube import extract_video_ids
from app.telegram_format import md_to_tg_html, safe_filename, split_message


def test_extract_ids():
    text = ("смотри https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=10 и https://youtu.be/abcdefghijk?si=x "
            "и youtube.com/shorts/ZZZZZZZZZZZ и m.youtube.com/watch?feature=share&v=dQw4w9WgXcQ "
            "и https://www.youtube.com/live/LLLLLLLLLLL")
    assert extract_video_ids(text) == ["dQw4w9WgXcQ", "abcdefghijk", "ZZZZZZZZZZZ", "LLLLLLLLLLL"]
    assert extract_video_ids("https://example.com/watch?v=dQw4w9WgXcQ") == []


def t(lang, gen):
    return NS(language_code=lang, is_generated=gen)


def test_pick_transcript():
    # ручные на языке оригинала важнее ручных на ru
    assert pick_transcript([t("ru", False), t("en", False), t("en", True)], ["ru"]).language_code == "en"
    # нет ручных на оригинале → ручные на предпочитаемом языке
    assert pick_transcript([t("ru", False), t("de", True)], ["ru"]).language_code == "ru"
    # только авто
    assert pick_transcript([t("de", True)], ["ru"]).is_generated
    assert pick_transcript([], ["ru"]) is None


def test_timestamps():
    tr = Transcript("x", "ru", True, [Segment(5.2, "привет"), Segment(3725, "мир")], "t")
    assert tr.as_timestamped() == "[00:05] привет\n[1:02:05] мир"


def test_md_to_html():
    out = md_to_tg_html("**О чём** <b>видео</b>\n- пункт *важно*\n# Заголовок")
    assert out == "<b>О чём</b> &lt;b&gt;видео&lt;/b&gt;\n• пункт <i>важно</i>\n<b>Заголовок</b>"


def test_split():
    parts = split_message("\n".join(["x" * 100] * 100), limit=1000)
    assert all(len(p) <= 1000 for p in parts) and "".join(parts).count("x") == 10000
    assert split_message("a" * 2500, limit=1000) == ["a" * 1000, "a" * 1000, "a" * 500]


def test_filename():
    assert safe_filename('Как: "учить" / Python?', "конспект.md") == "Как учить Python — конспект.md"


def _fake_claude(tmp_path, payload: dict, code=0):
    p = tmp_path / "claude"
    p.write_text(f"#!/bin/sh\ncat > {tmp_path}/stdin.txt\necho \"$@\" > {tmp_path}/args.txt\n"
                 f"echo '{json.dumps(payload)}'\nexit {code}\n")
    p.chmod(p.stat().st_mode | stat.S_IEXEC)
    return str(p)


async def test_claude_cli_ok(tmp_path):
    prov = ClaudeCliProvider(binary=_fake_claude(tmp_path, {"is_error": False, "result": "конспект"}))
    assert await prov.complete("sys", "текст") == "конспект"
    assert (tmp_path / "stdin.txt").read_text() == "текст"
    assert "--tools" in (tmp_path / "args.txt").read_text()


async def test_claude_cli_error(tmp_path):
    prov = ClaudeCliProvider(binary=_fake_claude(tmp_path, {"is_error": True, "result": "limit"}))
    with pytest.raises(LLMError):
        await prov.complete("sys", "текст")
