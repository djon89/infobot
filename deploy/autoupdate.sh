#!/usr/bin/env bash
# Автообновление: если в GitHub появился новый код — забрать и перезапустить бота.
# Запускается таймером systemd каждые 2 минуты. Настройки (.env) не трогает.
set -euo pipefail

# всё в функции: bash прочитает скрипт целиком до того, как git reset его перезапишет
main() {
  cd /opt/infobot
  git fetch -q origin main
  [ "$(git rev-parse HEAD)" = "$(git rev-parse origin/main)" ] && return 0
  echo "Обновляюсь: $(git rev-parse --short HEAD) → $(git rev-parse --short origin/main)"
  git reset -q --hard origin/main
  # если сборка упадёт — продолжит работать старая версия
  docker compose up -d --build --remove-orphans
  docker image prune -f >/dev/null
  echo "Готово: $(git log -1 --format='%h %s')"
}
main "$@"
exit
