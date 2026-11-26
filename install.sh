#!/usr/bin/env bash
# Установка InfoBot на чистый сервер Ubuntu одной командой:
#   curl -fsSL https://raw.githubusercontent.com/djon89/infobot/main/install.sh | sudo bash
set -euo pipefail

REPO_URL="${REPO_URL:-https://github.com/djon89/infobot.git}"
DIR=/opt/infobot
G='\033[1;32m'; Y='\033[1;33m'; R='\033[1;31m'; N='\033[0m'
say()  { echo -e "${G}==>${N} $*"; }
warn() { echo -e "${Y}!!${N} $*"; }
die()  { echo -e "${R}Ошибка:${N} $*"; exit 1; }
ask()  { local v; read -r -p "$1" v < /dev/tty; echo "$v"; }

[ "$(id -u)" -eq 0 ] || die "запустите от root (через sudo)"

say "1/6 Ставлю Docker и git (пара минут)…"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq && apt-get install -y -qq git curl python3 >/dev/null
command -v docker >/dev/null || curl -fsSL https://get.docker.com | sh >/dev/null

say "2/6 Скачиваю код бота…"
if [ -d "$DIR/.git" ]; then git -C "$DIR" pull -q; else git clone -q "$REPO_URL" "$DIR"; fi
cd "$DIR"

if [ -f .env ] && [ "${RECONFIGURE:-0}" != 1 ]; then
  say "Настройки уже есть (.env) — пропускаю вопросы. Перенастроить: RECONFIGURE=1"
else
  say "3/6 Токен Telegram-бота"
  echo "   Возьмите его у @BotFather (выглядит как 123456789:AAE...)."
  while :; do
    BOT_TOKEN=$(ask "   Вставьте токен и нажмите Enter: " | tr -d '[:space:]')
    NAME=$(curl -fsS "https://api.telegram.org/bot${BOT_TOKEN}/getMe" 2>/dev/null \
      | python3 -c 'import sys,json; print(json.load(sys.stdin)["result"]["username"])' 2>/dev/null) && break
    warn "Telegram не принял токен, попробуйте ещё раз."
  done
  say "Бот @$NAME найден."

  say "4/6 Определяю ваш Telegram ID"
  echo -e "   Откройте в Telegram ${G}https://t.me/$NAME${N} и нажмите «Старт» (или отправьте любое сообщение)."
  curl -fsS "https://api.telegram.org/bot${BOT_TOKEN}/deleteWebhook" >/dev/null || true
  OWNER_ID=""; OFFSET=0
  for _ in $(seq 1 60); do
    RESP=$(curl -fsS "https://api.telegram.org/bot${BOT_TOKEN}/getUpdates?timeout=5&offset=$OFFSET" || echo '{}')
    OWNER_ID=$(echo "$RESP" | python3 -c '
import sys,json
d=json.load(sys.stdin).get("result",[])
ids=[u["message"]["from"]["id"] for u in d if "message" in u]
print(ids[-1] if ids else "")' 2>/dev/null || true)
    [ -n "$OWNER_ID" ] && break
  done
  [ -n "$OWNER_ID" ] || OWNER_ID=$(ask "   Не дождался сообщения. Введите ID вручную (узнать у @userinfobot): ")
  say "Ваш ID: $OWNER_ID"

  say "5/6 Прокси против блокировок YouTube (Webshare → Proxy Settings)"
  echo "   Если прокси пока нет — просто нажмите Enter, добавим позже."
  WS_USER=$(ask "   Proxy Username: " | tr -d '[:space:]')
  WS_PASS=""
  [ -n "$WS_USER" ] && WS_PASS=$(ask "   Proxy Password: " | tr -d '[:space:]')

  cp .env.example .env
  python3 - "$BOT_TOKEN" "$OWNER_ID" "$WS_USER" "$WS_PASS" <<'PY'
import sys, re
tok, owner, wu, wp = sys.argv[1:]
vals = {"BOT_TOKEN": tok, "OWNER_IDS": owner, "ACCESS_MODE": "allowlist",
        "WEBSHARE_PROXY_USERNAME": wu, "WEBSHARE_PROXY_PASSWORD": wp}
lines = open(".env").read().splitlines()
out = []
for l in lines:
    k = l.split("=", 1)[0]
    out.append(f"{k}={vals[k]}" if k in vals else l)
open(".env", "w").write("\n".join(out) + "\n")
PY
  chmod 600 .env
fi

say "Собираю бота (3–5 минут)…"
docker compose build -q

if ! grep -qE '^CLAUDE_CODE_OAUTH_TOKEN=.+' .env; then
  say "6/6 Подключаю вашу подписку Claude"
  echo "   Сейчас появится ссылка: откройте её в браузере, войдите в Claude,"
  echo "   скопируйте код и вставьте сюда. В конце Claude покажет длинный токен"
  echo "   (начинается с sk-ant-oat) — скопируйте его целиком."
  docker compose run --rm worker claude setup-token < /dev/tty || true
  CT=$(ask "   Вставьте токен sk-ant-oat…: " | tr -d '[:space:]')
  sed -i "s|^CLAUDE_CODE_OAUTH_TOKEN=.*|CLAUDE_CODE_OAUTH_TOKEN=$CT|" .env
fi

say "Запускаю…"
docker compose up -d
sleep 5
if docker compose run --rm -T worker sh -c 'echo "Ответь одним словом: работает" | claude -p --tools "" 2>&1' | tail -1 | grep -qi "работ"; then
  say "Claude отвечает ✓"
else
  warn "Claude пока не ответил. Чтобы ввести токен заново:"
  warn "  sed -i 's/^CLAUDE_CODE_OAUTH_TOKEN=.*/CLAUDE_CODE_OAUTH_TOKEN=/' $DIR/.env  и запустите установку ещё раз"
fi
docker compose ps --format '{{.Service}}: {{.State}}'
echo
say "Готово! Пришлите боту ссылку на YouTube-видео."
echo "   Логи:      cd $DIR && docker compose logs -f worker"
echo "   Обновить:  curl -fsSL https://raw.githubusercontent.com/djon89/infobot/main/install.sh | sudo bash"
