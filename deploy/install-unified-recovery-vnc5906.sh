#!/usr/bin/env bash
# Safe VNC5906 watchdog: preserve the existing live listener's author IDs and cutover.
set -Eeuo pipefail
umask 077
mode=inspect
if (( $# )); then mode="$1"; fi
[[ "$mode" == inspect || "$mode" == install || "$mode" == status ]] || { echo "Use: {inspect|install|status}" >&2; exit 2; }
[[ $(id -u) == 0 ]] || { echo "Run as root" >&2; exit 1; }
unit=/etc/systemd/system/sktrader-discord-limit-live.service
live=sktrader-discord-limit-live.service
override=/etc/systemd/system/sktrader-discord-limit-live.service.d/20-watchdog-heartbeat.conf
health=/var/lib/dkcapital-desktop/.local/share/sktrader/discord-limit-live-health.json
home=/var/lib/dkcapital-desktop
src=/opt/sktrader/deploy
recovery=dkcapital-unified-recovery
fail() { echo "ERROR: $*" >&2; exit 1; }
inspect() {
  systemctl is-active "$live" || true
  systemctl is-active "$recovery.timer" || true
  if [[ -e "$override" ]]; then echo "Gateway heartbeat override installed"; fi
  python3 "$src/unified_recovery_vnc5906.py" status
}
if [[ "$mode" != install ]]; then
  inspect
  if [[ "$mode" == status ]]; then journalctl -u "$recovery.service" -n 40 --no-pager || true; fi
  exit 0
fi
[[ -f "$unit" ]] || fail "Existing live production listener missing"
[[ -f "$src/unified_recovery_vnc5906.py" ]] || fail "Recovery script missing"
[[ -f "$src/$recovery.service" && -f "$src/$recovery.timer" ]] || fail "Recovery unit source missing"
[[ ! -e "$override" && ! -L "$override" ]] || fail "Recovery override already exists"
[[ ! -e "/etc/systemd/system/$recovery.service" && ! -e "/etc/systemd/system/$recovery.timer" ]] || fail "Recovery units already exist"
systemctl is-active --quiet "$live" || fail "Live Discord listener inactive"
[[ -s "$home/.wine-mt5/drive_c/users/dkvnc/AppData/Roaming/MetaQuotes/Terminal/Common/Files/discord_limit_account.csv" ]] || fail "MT5 broker telemetry missing"
[[ "$(grep -c '^ExecStart=' "$unit")" == 1 ]] || fail "Unexpected production ExecStart"
line=$(grep '^ExecStart=' "$unit")
[[ "$line" == *"sktrader.discord_limit_bridge "* ]] || fail "Unexpected production process"
[[ "$line" == *"--channel-id 1565527578163552327 "* ]] || fail "Unexpected source channel"
[[ "$line" == *"--author-id 1554225452091048017 --author-id 508740193369587722 "* ]] || fail "Unexpected source author IDs"
[[ "$line" == *"--not-before-ms "* ]] || fail "Live cutover missing"
[[ "$line" == *"--database $home/.local/share/sktrader/discord-limit-live.db "* ]] || fail "Unexpected live DB"
[[ "$line" == *"--output $home/.wine-mt5/drive_c/users/dkvnc/AppData/Roaming/MetaQuotes/Terminal/Common/Files/discord_limit_30m_signals.csv "* ]] || fail "Wrong MT5 signal target"
[[ "$line" != *"--health-file "* && "$line" != *"--max-input-age-sec "* ]] || fail "Health flags already present"
install -d -m 0755 -o root -g root /etc/systemd/system/sktrader-discord-limit-live.service.d
cat > "$override" <<EOF
[Service]
ExecStart=
$line --health-file $health --max-input-age-sec 60
EOF
chmod 0644 "$override"
install -m 0644 -o root -g root "$src/$recovery.service" "/etc/systemd/system/$recovery.service"
install -m 0644 -o root -g root "$src/$recovery.timer" "/etc/systemd/system/$recovery.timer"
systemctl daemon-reload
systemctl restart "$live"
systemctl is-active --quiet "$live" || fail "Live listener failed"
good=0
for i in $(seq 1 25); do
  if [[ -s "$health" ]] && python3 - "$health" <<'PY'
import json,sys,time
a=json.load(open(sys.argv[1]))
assert a["status"]=="ready"
assert 0<=time.time()-a["updated_at"]<15
assert 0<=time.time()-a["last_ack_at"]<15
print("Discord Gateway heartbeat READY, pid",a["pid"])
PY
  then good=1; break; fi
  sleep 1
done
(( good )) || fail "No fresh READY heartbeat. Timer not enabled. Inspect live listener journal."
systemctl enable --now "$recovery.timer"
systemctl is-active --quiet "$recovery.timer" || fail "Recovery timer failed"
echo "RECOVERY INSTALLED: monitoring Discord, MT5, VNC and JIFU every ~30s."
echo "Never automatically kills a running MT5 process or touches GWS/AUROUX."
echo "Disable recovery timer BEFORE intentionally stopping the live Discord feed."
