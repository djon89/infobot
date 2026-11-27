"""Официальный Claude API — для публичных пользователей."""
import anthropic

from .base import LLMError


class AnthropicProvider:
    name = "anthropic"

    def __init__(self, api_key: str, model: str, effort: str = "medium"):
        self._client = anthropic.AsyncAnthropic(api_key=api_key)
        self._model = model
        self._effort = effort

    async def complete(self, system: str, prompt: str) -> str:
        return await self._run(system, prompt, {"effort": self._effort})

    async def complete_json(self, system: str, prompt: str, schema: dict) -> dict:
        import json
        text = await self._run(system, prompt, {"effort": self._effort,
                                                "format": {"type": "json_schema", "schema": schema}})
        try:
            return json.loads(text)
        except json.JSONDecodeError as e:
            raise LLMError("Модель вернула неполный ответ.") from e

    async def _run(self, system: str, prompt: str, output_config: dict) -> str:
        try:
            async with self._client.beta.messages.stream(
                model=self._model,
                max_tokens=32000,
                system=system,
                messages=[{"role": "user", "content": prompt}],
                output_config=output_config,
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
            ) as stream:
                msg = await stream.get_final_message()
        except anthropic.RateLimitError as e:
            raise LLMError("Лимит запросов к модели, попробуйте позже.") from e
        except anthropic.APIStatusError as e:
            raise LLMError(f"Ошибка модели: {e.status_code}") from e
        except anthropic.APIConnectionError as e:
            raise LLMError("Нет связи с моделью.") from e
        if msg.stop_reason == "max_tokens":
            raise LLMError("Ответ модели не поместился в лимит.")
        if msg.stop_reason == "refusal":
            raise LLMError("Модель отказалась обрабатывать этот текст.")
        text = "".join(b.text for b in msg.content if b.type == "text").strip()
        if not text:
            raise LLMError("Модель вернула пустой ответ.")
        return text
