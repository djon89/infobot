#!/usr/bin/env bash
# Разовая настройка, после которой терминал больше не нужен:
#  1) автообновление бота из GitHub;
#  2) Claude Code на сервере с Remote Control — управление сервером из приложения Claude.
set -euo pipefail
G='\033[1;32m'; Y='\033[1;33m'; N='\033[0m'
say()  { echo -e "${G}==>${N} $*"; }
warn() { echo -e "${Y}!!${N} $*"; }
[ "$(id -u)" -eq 0 ] || { echo "Запустите от root"; exit 1; }
cd /opt/infobot && git pull -q

say "1/4 Обновляю бота до последней версии…"
bash install.sh

say "2/4 Включаю автообновление (каждые 2 минуты)…"
chmod +x deploy/autoupdate.sh
cat > /etc/systemd/system/infobot-autoupdate.service <<UNIT
[Unit]
Description=InfoBot auto-update from GitHub
After=network-online.target docker.service
[Service]
Type=oneshot
ExecStart=/opt/infobot/deploy/autoupdate.sh
UNIT
cat > /etc/systemd/system/infobot-autoupdate.timer <<UNIT
[Unit]
Description=InfoBot auto-update every 2 minutes
[Timer]
OnBootSec=1min
OnUnitActiveSec=2min
[Install]
WantedBy=timers.target
UNIT
systemctl daemon-reload
systemctl enable --now infobot-autoupdate.timer >/dev/null
say "   Автообновление включено ✓"

say "3/4 Ставлю Claude Code на сервер…"
apt-get install -y -qq tmux >/dev/null 2>&1 || true
export PATH="$HOME/.local/bin:$PATH"
command -v claude >/dev/null || curl -fsSL https://claude.ai/install.sh | bash
command -v claude >/dev/null || { warn "Claude Code не установился — пришлите этот вывод"; exit 1; }
grep -q '.local/bin' ~/.bashrc || echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.bashrc

if ! claude auth status >/dev/null 2>&1; then
  say "   Вход в Claude: откройте ссылку, войдите, скопируйте код и вставьте сюда."
  claude auth login < /dev/tty
fi

say "4/4 Запускаю Remote Control…"
RC_CMD="cd /opt/infobot && $HOME/.local/bin/claude remote-control --name infobot-server"
tmux kill-session -t rc 2>/dev/null || true
tmux new-session -d -s rc "$RC_CMD"
# автозапуск после перезагрузки сервера
( crontab -l 2>/dev/null | grep -v 'tmux new-session -d -s rc' ; \
  echo "@reboot sleep 20 && /usr/bin/tmux new-session -d -s rc '$RC_CMD'" ) | crontab -
sleep 15
echo "----- экран Remote Control -----"
tmux capture-pane -p -t rc | sed '/^\s*$/d' | tail -n 20
echo "--------------------------------"
say "Если выше вопрос (y/n или выбор) — выполните:  tmux attach -t rc"
say "ответьте на него, затем нажмите Ctrl+B, потом D (выйти, не останавливая)."
say "Готово! Терминал можно закрыть."
