import os

import pytest

os.environ.setdefault("BOT_TOKEN", "123:test")


@pytest.fixture(autouse=True)
def _no_network_thumbnails(monkeypatch):
    """В тестах не ходим в сеть за обложками."""
    async def fake(video_id):
        return f"https://i.ytimg.com/vi/{video_id}/maxresdefault.jpg"
    monkeypatch.setattr("app.worker.tasks.pick_thumbnail", fake)
