from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import Message

from app.config import Settings


class AccessMiddleware(BaseMiddleware):
    def __init__(self, settings: Settings):
        self.s = settings

    async def __call__(self, handler: Callable[[Message, dict], Awaitable[Any]], event: Message, data: dict):
        u = event.from_user
        if u is None:
            return None
        if self.s.access_mode == "allowlist":
            allowed = set(self.s.owner_ids) | set(self.s.allowlist_ids)
            if u.id not in allowed:
                await event.answer(f"Бот работает в закрытом режиме. Ваш ID: {u.id}")
                return None
        return await handler(event, data)
