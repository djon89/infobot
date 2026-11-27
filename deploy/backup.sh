#!/bin/sh
# Резервная копия базы раз в сутки, хранится 14 дней. Запускается в контейнере backup.
set -eu
mkdir -p /backups
while true; do
  f="/backups/bot-$(date +%Y%m%d-%H%M).sql.gz"
  if pg_dump -h postgres -U bot bot | gzip > "$f.tmp"; then mv "$f.tmp" "$f"; echo "backup ok: $f"; else rm -f "$f.tmp"; echo "backup FAILED"; fi
  find /backups -name 'bot-*.sql.gz' -mtime +14 -delete
  sleep 86400
done
