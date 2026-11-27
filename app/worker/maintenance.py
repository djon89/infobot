"""Обслуживание: еженедельная отправка резервной копии базы владельцам в Telegram."""
import glob
import logging
import os

from aiogram.types import FSInputFile

log = logging.getLogger(__name__)
BACKUP_DIR = "/backups"


async def send_backup(ctx):
    files = sorted(glob.glob(os.path.join(BACKUP_DIR, "bot-*.sql.gz")))
    if not files:
        log.warning("no database backups found in %s", BACKUP_DIR)
        return
    latest = files[-1]
    size = os.path.getsize(latest)
    if size > 45 * 1024 * 1024:
        log.warning("backup %s is too big for Telegram (%d bytes)", latest, size)
        return
    for uid in ctx["settings"].owner_ids:
        await ctx["bot"].send_document(uid, FSInputFile(latest), caption=(
            "💾 Еженедельная резервная копия базы бота. Храните её — по ней можно восстановить "
            "пользователей, каналы и конспекты."), disable_notification=True)
