#!/usr/bin/env bash
# Provision a NON-TRADING Discord listener for the isolated MT5 VNC 5906 desktop.
# Preflight and monitor use a quarantine CSV OUTSIDE MT5 Common/Files.
# Never enable broker orders or automatically point the bridge at MT5.
set -Eeuo pipefail
umask 077

action=inspect
if (( $# )); then action="$1"; fi
[[ "$action" == inspect || "$action" == prepare-monitor || "$action" == status ]] ||
  { echo "Usage: sudo bash deploy/setup-discord-limit-vnc5906.sh {inspect|prepare-monitor|status}" >&2; exit 2; }
[[ $(id -u) == 0 ]] || { echo "Run as root via sudo" >&2; exit 1; }

user=dkvnc
home=/var/lib/dkcapital-desktop
prefix="$home/.wine-mt5"
mt5common="$prefix/drive_c/users/dkvnc/AppData/Roaming/MetaQuotes/Terminal/Common/Files"
unit=/etc/systemd/system/sktrader-discord-limit-monitor.service
existing_service=sktrader-discord-limit-30m.service
service=sktrader-discord-limit-monitor.service
old_token=/var/lib/dkcapital-mt5-3/.local/share/sktrader/discord-bot-token
private="$home/.local/share/sktrader"
token="$private/discord-bot-token"
output="$private/discord-limit-monitor.csv"
db="$private/discord-limit-monitor.db"
venv="$home/.venv-discord-limit"
python="$venv/bin/python"
channel=1565527578163552327

die() { echo "ERROR: $*" >&2; exit 1; }

inspect() {
  echo "===== MT5 VNC 5906 ====="
  systemctl is-active dkcapital-desktop-vnc.service || true
  [[ -d "$mt5common" ]] && echo "MT5 Common/Files exists: $mt5common" ||
    echo "MT5 Common/Files not found"
  echo "===== DISCORD BOT TOKEN AVAILABILITY (CONTENTS NEVER PRINTED) ====="
  for path in "$old_token" "$token"; do
    if [[ -s "$path" ]]; then
      stat -c 'PRESENT %n owner=%U perms=%a size=%s' "$path"
    else
      echo "MISSING $path"
    fi
  done
  echo "===== ISOLATED BRIDGE PYTHON ====="
  if [[ -x "$python" ]]; then
    if runuser -u "$user" -- env HOME="$home" PYTHONPATH=/opt/sktrader \
      "$python" -c 'import websockets, sktrader.discord_limit_bridge; print("Isolated Discord bridge dependencies OK", websockets.__version__)'; then
      :
    else
      echo "Isolated Python dependencies could not be loaded"
    fi
  else
    echo "Dedicated Discord bridge virtualenv not yet created: $python"
  fi
  echo "===== SERVICES ====="
  for name in "$service" "$existing_service" sktrader.service; do
    printf '%s: ' "$name"; systemctl is-active "$name" 2>/dev/null || true
  done
  [[ -f "$output" ]] && stat -c 'Monitor CSV: %n, size=%s, updated=%y' "$output" ||
    echo "Monitor CSV not yet created"
  [[ -f "$mt5common/discord_limit_30m_signals.csv" ]] &&
    echo "WARNING: actionable MT5 signal CSV already exists; inspect EA arming!" ||
    echo "No actionable MT5 signal CSV in selected Common/Files."
}

if [[ "$action" == inspect ]]; then inspect; exit 0; fi
if [[ "$action" == status ]]; then
  inspect
  echo "===== MOST RECENT NON-TRADING LISTENER LOGS ====="
  journalctl -u "$service" -n 35 --no-pager 2>/dev/null || true
  exit 0
fi

[[ -f /etc/systemd/system/dkcapital-desktop-vnc.service ]] ||
  die "Isolated VNC service not installed"
grep -Fq 'User=dkvnc' /etc/systemd/system/dkcapital-desktop-vnc.service ||
  die "Unknown VNC owner"
grep -Fq -- '-rfbport 5906 -localhost yes' /etc/systemd/system/dkcapital-desktop-vnc.service ||
  die "Unknown VNC port/binding"
systemctl is-active --quiet dkcapital-desktop-vnc.service ||
  die "VNC desktop is not active"
[[ "$(getent passwd "$user" | cut -d: -f6)" == "$home" ]] ||
  die "Wrong dkvnc home"
[[ -d "$mt5common" ]] || die "New MT5 Common/Files directory missing"
[[ -f /opt/sktrader/sktrader/discord_limit_bridge.py ]] ||
  die "Discord bridge source missing"
command -v python3 >/dev/null || die "System Python 3 missing"
if [[ -e "$venv" ]]; then
  [[ ! -L "$venv" && -d "$venv" ]] || die "Isolated Python virtualenv path is not a directory"
  [[ "$(stat -c %u "$venv")" == "$(id -u "$user")" ]] ||
    die "Existing isolated Python virtualenv owned by another account"
else
  echo "===== CREATE NEW ISOLATED DISCORD PYTHON ENVIRONMENT ====="
  if ! runuser -u "$user" -- env HOME="$home" python3 -m venv "$venv"; then
    die "Python venv creation failed; check python3-venv. Existing Sktrader venv is unchanged"
  fi
fi
[[ -x "$python" ]] || die "Dedicated Discord Python executable missing: $python"
echo "===== INSTALL ONLY WEBSOCKETS IN DEDICATED ENVIRONMENT ====="
runuser -u "$user" -- env HOME="$home" \
  "$python" -m pip install --disable-pip-version-check --no-cache-dir --no-input \
  'websockets>=14,<17' ||
  die "Cannot install websockets into isolated virtualenv"
runuser -u "$user" -- env HOME="$home" PYTHONPATH=/opt/sktrader \
  "$python" -c 'import websockets, sktrader.discord_limit_bridge; print("Isolated bridge ready, websockets", websockets.__version__)' ||
  die "Discord bridge dependencies still unavailable in isolated virtualenv"
if [[ -e "$unit" || -L "$unit" ]]; then
  die "Monitor unit already exists. Refusing overwrite"
fi
if [[ "$(systemctl show "$service" -p LoadState --value)" != not-found ]]; then
  die "Monitor unit has a pre-existing systemd definition; refusing override"
fi
if systemctl is-active --quiet "$existing_service"; then
  die "An older actionable Discord listener is active; refusing duplicate"
fi
if [[ -e "$mt5common/discord_limit_30m_signals.csv" ]]; then
  die "An actionable MT5 signal CSV already exists; verify the EA and investigate first"
fi
if [[ -d "$private" && "$(stat -c %u "$private")" != "$(id -u "$user")" ]]; then
  die "Monitor state directory is not owned by dkvnc"
fi
if [[ -e "$token" ]]; then
  [[ -s "$token" ]] || die "Existing private token is empty"
  [[ "$(stat -c %u "$token")" == "$(id -u "$user")" ]] ||
    die "Existing private token owner is not dkvnc"
else
  [[ -s "$old_token" ]] ||
    die "Existing Discord bot token not at expected old source. Run inspect; do not paste tokens in chat."
fi

install -d -o "$user" -g "$user" -m 0700 "$private"
if [[ ! -e "$token" ]]; then
  install -o "$user" -g "$user" -m 0600 "$old_token" "$token"
fi
chmod 0600 "$token"
echo "===== VERIFY BOT CAN READ PINNED CHANNEL (NO MESSAGES SENT) ====="
runuser -u "$user" -- env HOME="$home" PYTHONPATH=/opt/sktrader "$python" - "$token" "$channel" <<'PY'
import sys
from pathlib import Path
from sktrader.discord_limit_bridge import bot_profile
token=Path(sys.argv[1]).read_text(encoding="utf-8").strip()
assert token
bot_id=bot_profile(token,sys.argv[2])
print("Discord bot ID:",bot_id)
print("Source channel:",sys.argv[2])
print("IMPORTANT: listener defaults to messages authored by THIS bot.")
print("If actual signal author differs, configure an explicit trusted author first.")
PY

cat > "$unit" <<'UNIT'
[Unit]
Description=DK Capital Discord limit listener, MONITOR ONLY, never feeds MT5
Wants=network-online.target
After=network-online.target

[Service]
Type=simple
User=dkvnc
Group=dkvnc
WorkingDirectory=/opt/sktrader
Environment=HOME=/var/lib/dkcapital-desktop
Environment=PYTHONUNBUFFERED=1
ExecStart=/var/lib/dkcapital-desktop/.venv-discord-limit/bin/python -u -m sktrader.discord_limit_bridge --channel-id 1565527578163552327 --token-file /var/lib/dkcapital-desktop/.local/share/sktrader/discord-bot-token --database /var/lib/dkcapital-desktop/.local/share/sktrader/discord-limit-monitor.db --output /var/lib/dkcapital-desktop/.local/share/sktrader/discord-limit-monitor.csv
Restart=always
RestartSec=5
NoNewPrivileges=true
UMask=0077
MemoryMax=400M
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
UNIT
chmod 0644 "$unit"
systemctl daemon-reload
systemctl enable --now "$service"
systemctl is-active --quiet "$service" || {
  journalctl -u "$service" -n 40 --no-pager >&2
  die "Non-trading Discord listener did not become active"
}
echo "MONITOR LISTENER STARTED, trading NOT connected."
echo "Output is isolated: $output"
echo "Selected MT5 Common/Files is deliberately UNTOUCHED: $mt5common"
echo "Check: sudo journalctl -u $service -n 40 --no-pager"
echo "Do not enable Algo Trading until author, real messages, path and limits are validated."
