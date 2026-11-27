"""/stats для владельца: что делает бот и не начал ли YouTube блокировать сервер."""
from datetime import datetime, timedelta, timezone

from sqlalchemy import case, func, select

from app.db.models import Channel, DownloadLog, Job, User, UserChannel
from app.services.downloader import human_size


async def build_stats(s) -> str:
    now = datetime.now(timezone.utc)
    periods = [("24 ч", now - timedelta(days=1)), ("7 дн", now - timedelta(days=7)), ("30 дн", now - timedelta(days=30))]
    lines = ["📈 <b>Статистика бота</b>", ""]

    def jobs_q(kind, since):
        return select(
            func.count().filter(Job.status == "done"),
            func.count().filter(Job.status == "failed"),
            func.count().filter(Job.error.ilike("%Blocked%") | Job.error.ilike("%заблокировал%")),
        ).where(Job.kind == kind, Job.created_at >= since)

    def dl_q(since):
        return select(
            func.count().filter(DownloadLog.ok & ~DownloadLog.cached),
            func.coalesce(func.sum(case((DownloadLog.ok & ~DownloadLog.cached, DownloadLog.bytes), else_=0)), 0),
            func.count().filter(DownloadLog.cached),
            func.count().filter(DownloadLog.via_proxy),
            func.count().filter(DownloadLog.direct_blocked),
            func.count().filter(~DownloadLog.ok),
            func.coalesce(func.sum(case((DownloadLog.via_proxy, DownloadLog.bytes), else_=0)), 0),
        ).where(DownloadLog.created_at >= since)

    for label, since in periods:
        sd, sf, sb = (await s.execute(jobs_q("youtube_summary", since))).one()
        dd, _, _ = (await s.execute(jobs_q("deep_dive", since))).one()
        n, size, cached, proxy, blocked, failed, proxy_bytes = (await s.execute(dl_q(since))).one()
        lines.append(f"<b>За {label}</b>")
        lines.append(f"📝 Конспекты: {sd}" + (f" (ошибок {sf}" + (f", блокировок YouTube {sb}" if sb else "") + ")" if sf else ""))
        if dd:
            lines.append(f"🔍 Разборы частей: {dd}")
        lines.append(f"⬇️ Скачано: {n} · {human_size(int(size)) if size else '0 МБ'}"
                     f" (трафик сервера ≈ {human_size(int(size) * 2) if size else '0 МБ'}: туда и обратно)")
        if cached:
            lines.append(f"♻️ Без скачивания, из кэша Telegram: {cached}")
        if proxy:
            lines.append(f"🛡 Через прокси: {proxy} · {human_size(int(proxy_bytes))}")
        if blocked:
            lines.append(f"🚫 YouTube отказал серверу напрямую: {blocked} раз")
        if failed:
            lines.append(f"❌ Неудачных скачиваний: {failed}")
        lines.append("")

    users = (await s.execute(select(func.count()).select_from(User))).scalar_one()
    active = (await s.execute(select(func.count(func.distinct(Job.user_id)))
                              .where(Job.created_at >= now - timedelta(days=7)))).scalar_one()
    chans = (await s.execute(select(func.count(func.distinct(UserChannel.channel_id))))).scalar_one()
    broken = (await s.execute(select(func.count()).select_from(Channel)
                              .where(Channel.last_error.is_not(None)))).scalar_one()
    lines.append(f"👥 Пользователей: {users}, активных за неделю: {active}")
    lines.append(f"📺 Каналов под наблюдением: {chans}" + (f" (с ошибкой ленты: {broken})" if broken else ""))

    last_block = (await s.execute(select(func.max(DownloadLog.created_at)).where(DownloadLog.direct_blocked))).scalar_one()
    lines.append("")
    if last_block:
        lb = last_block if last_block.tzinfo else last_block.replace(tzinfo=timezone.utc)
        lines.append(f"⚠️ Последняя блокировка YouTube: {(now - lb).days} дн назад. "
                     "Если блокировки участятся — пора подключать прокси для скачивания.")
    else:
        lines.append("✅ YouTube ни разу не блокировал скачивание с сервера.")
    return "\n".join(lines)


