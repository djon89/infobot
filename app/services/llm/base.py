from typing import Protocol


class LLMError(Exception):
    pass


class LLMProvider(Protocol):
    name: str

    async def complete(self, system: str, prompt: str) -> str: ...

    async def complete_json(self, system: str, prompt: str, schema: dict) -> dict:
        """Ответ строго по JSON-схеме."""
        ...
