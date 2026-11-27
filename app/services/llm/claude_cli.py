"""Claude Code в headless-режиме (`claude -p`) на подписке владельца.

Только для личного использования владельцем: подписку Pro/Max нельзя
использовать для обслуживания других людей — для них AnthropicProvider.
Авторизация: переменная CLAUDE_CODE_OAUTH_TOKEN (получить: `claude setup-token`).
"""
import asyncio
import json

from .base import LLMError


class ClaudeCliProvider:
    name = "claude_cli"

    def __init__(self, model: str = "opus", timeout: int = 600, binary: str = "claude"):
        self._model = model
        self._timeout = timeout
        self._binary = binary

    async def complete(self, system: str, prompt: str) -> str:
        data = await self._run(system, prompt)
        text = (data.get("result") or "").strip()
        if not text:
            raise LLMError("Claude Code вернул пустой ответ.")
        return text

    async def complete_json(self, system: str, prompt: str, schema: dict) -> dict:
        data = await self._run(system, prompt, ["--json-schema", json.dumps(schema, ensure_ascii=False)])
        out = data.get("structured_output")
        if not isinstance(out, dict):
            try:
                out = json.loads(data.get("result") or "")
            except json.JSONDecodeError as e:
                raise LLMError("Claude Code вернул ответ не по схеме.") from e
        return out

    async def _run(self, system: str, prompt: str, extra: list[str] | None = None) -> dict:
        args = [
            self._binary, "-p",
            "--output-format", "json",
            "--model", self._model,
            "--system-prompt", system,
            "--tools", "",                 # никаких инструментов: только текст
            "--strict-mcp-config",
            "--setting-sources", "",
            "--no-session-persistence",
            *(extra or []),
        ]
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            out, err = await asyncio.wait_for(proc.communicate(prompt.encode()), self._timeout)
        except asyncio.TimeoutError:
            proc.kill()
            raise LLMError("Claude Code не ответил вовремя.")
        if proc.returncode != 0:
            raise LLMError(f"Claude Code завершился с ошибкой: {err.decode()[-500:] or out.decode()[-500:]}")
        try:
            data = json.loads(out)
        except json.JSONDecodeError as e:
            raise LLMError("Не удалось разобрать ответ Claude Code.") from e
        if data.get("is_error"):
            raise LLMError(f"Claude Code: {data.get('result') or data.get('subtype')}")
        return data
