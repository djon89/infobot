from app.config import Settings

from .anthropic_api import AnthropicProvider
from .base import LLMError, LLMProvider
from .claude_cli import ClaudeCliProvider


def build_provider(name: str, s: Settings) -> LLMProvider:
    if name == "anthropic":
        if not s.anthropic_api_key:
            raise LLMError("конспекты для этого пользователя пока не настроены (нет ключа API).")
        return AnthropicProvider(s.anthropic_api_key, s.anthropic_model, s.anthropic_effort)
    if name == "claude_cli":
        return ClaudeCliProvider(s.claude_cli_model, s.claude_cli_timeout)
    raise ValueError(f"Неизвестный LLM-провайдер: {name}")


class LLMRouter:
    """Владелец → свой провайдер (например, подписка Claude Code), остальные → публичный.
    Провайдеры создаются лениво: без API-ключа бот работает, пока им пользуется только владелец."""

    def __init__(self, s: Settings):
        self._s = s
        self._owner_ids = set(s.owner_ids)
        self._cache: dict[str, LLMProvider] = {}

    def _get(self, name: str) -> LLMProvider:
        if name not in self._cache:
            self._cache[name] = build_provider(name, self._s)
        return self._cache[name]

    def for_user(self, user_id: int) -> LLMProvider:
        if user_id in self._owner_ids:
            return self._get(self._s.llm_provider_owner)
        return self._get(self._s.llm_provider_public)
