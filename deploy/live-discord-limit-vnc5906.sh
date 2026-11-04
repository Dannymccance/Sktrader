#!/usr/bin/env bash
# Connect the already-verified Discord Gateway to ONE isolated MT5 terminal.
# Does not toggle MT5 Algo Trading or the EA EnableNewEntries input.
# Uses a FRESH trade-input DB and cutover epoch; cannot replay monitor tests.
set -Eeuo pipefail
umask 077

action="${1:-inspect}"
[[ "$action" == inspect || "$action" == stage || "$action" == start || "$action" == status ]] || {
  echo "Usage: sudo bash deploy/live-discord-limit-vnc5906.sh {inspect|stage|start|status}" >&2; exit 2;
}
[[ $(id -u) -eq 0 ]] || { echo "Run with sudo on VPS SSH" >&2; exit 1; }

user=dkvnc
home=/var/lib/dkcapital-desktop
prefix="$home/.wine-mt5"
common="$prefix/drive_c/users/dkvnc/AppData/Roaming/MetaQuotes/Terminal/Common/Files"
telemetry="$common/discord_limit_account.csv"
feed="$common/discord_limit_30m_signals.csv"
private="$home/.local/share/sktrader"
token="$private/discord-bot-token"
python="$home/.venv-discord-limit/bin/python"
db="$private/discord-limit-live.db"
unit=/etc/systemd/system/sktrader-discord-limit-live.service
service=sktrader-discord-limit-live.service
monitor=sktrader-discord-limit-monitor.service
legacy=sktrader-discord-limit-30m.service
channel=1565527578163552327
bot_author=1554225452091048017
human_author=508740193369587722
die() { echo "ERROR: $*" >&2; exit 1; }

check_account() {
  [[ -s "$telemetry" ]] || die "Live EA telemetry missing: $telemetry"
  python3 - "$telemetry" <<'PY'
import csv,datetime,sys,time
with open(sys.argv[1],encoding="utf-8-sig",newline="") as f:
    rows=list(csv.DictReader(f,delimiter=";"))
if len(rows)!=1:
    raise SystemExit("Expected one broker telemetry account row")
a=rows[0]
try:
    login=int(a["login"]); stamp=int(a["generated_at"]); balance=float(a["balance"])
except (ValueError,KeyError,TypeError):
    raise SystemExit("Malformed MT5 account telemetry")
age=time.time()-stamp
if login<=0 or not 0<=age<=90 or balance<=0:
    raise SystemExit(f"Invalid/stale MT5 account snapshot: login={login}, age_seconds={age:.1f}, balance={balance}")
if a.get("mode") not in {"DEMO","REAL","CONTEST"} or not a.get("server") or not a.get("currency"):
    raise SystemExit("Missing connected MT5 account identity")
print(f"Connected MT5 account: login={login} mode={a['mode']} server={a['server']} currency={a['currency']} balance={balance:.2f}")
print("NOTE: feed is account-agnostic; this check only confirms a fresh, connected terminal.")
PY
}

check_paths() {
  [[ -f /etc/systemd/system/dkcapital-desktop-vnc.service ]] || die "Isolated desktop VNC service missing"
  grep -Fq "User=dkvnc" /etc/systemd/system/dkcapital-desktop-vnc.service || die "Unexpected VNC user"
  grep -Fq -- "-rfbport 5906 -localhost yes" /etc/systemd/system/dkcapital-desktop-vnc.service || die "Unexpected VNC port"
  systemctl is-active --quiet dkcapital-desktop-vnc.service || die "Isolated VNC desktop not running"
  [[ -d "$common" && "$(stat -c %u "$common")" == "$(id -u "$user")" ]] || die "MT5 Common/Files directory invalid"
  [[ -x "$python" && -s "$token" ]] || die "Isolated Discord Python or private bot token missing"
  [[ "$(stat -c %u "$token")" == "$(id -u "$user")" ]] || die "Bot token owner mismatch"
  [[ "$(stat -c %a "$token")" == 600 || "$(stat -c %a "$token")" == 400 ]] || die "Bot token not owner-only"
  [[ -d "$private" && "$(stat -c %u "$private")" == "$(id -u "$user")" ]] || die "Isolated Discord state directory invalid"
  runuser -u "$user" -- env HOME="$home" PYTHONPATH=/opt/sktrader "$python" -c 'import websockets,sktrader.discord_limit_bridge' || die "Isolated bridge imports failed"
  systemctl is-active --quiet "$legacy" && die "Legacy actionable Discord service already active; refuse duplicate feeds"
  [[ ! -e "$feed" && ! -L "$feed" ]] || die "Actionable MT5 signal file already exists; inspect before configuring a second feed"
  [[ ! -e "$db" && ! -L "$db" ]] || die "Live signal database already exists; refusing replay/overwrite"
  [[ ! -e "$unit" && ! -L "$unit" ]] || die "Live systemd unit exists; use status/start, do not overwrite"
}

inspect() {
  echo "===== TERMINAL ====="
  if [[ -f "$telemetry" ]]; then
    check_account || true
  else
    echo "MT5 account telemetry missing"
  fi
  echo "===== FEED STATUS ====="
  for svc in "$service" "$monitor" "$legacy" sktrader.service; do
    printf "%s: " "$svc"; systemctl is-active "$svc" 2>/dev/null || true
  done
  for path in "$feed" "$db" "$unit"; do
    if [[ -e "$path" ]]; then stat -c "EXISTS: %n bytes=%s owner=%U" "$path"; else echo "Missing: $path"; fi
  done
  echo "Trusted Discord IDs: bot=$bot_author personal=$human_author"
}

if [[ "$action" == inspect || "$action" == status ]]; then
  inspect
  if [[ "$action" == status ]]; then
    echo "===== LIVE FEED RECENT LOGS ====="
    journalctl -u "$service" -n 35 --no-pager 2>/dev/null || true
  fi
  exit 0
fi

if [[ "$action" == stage ]]; then
  check_paths
  check_account
  [[ -f /etc/systemd/system/"$monitor" ]] || die "Previously verified Discord monitor not installed"
  systemctl is-active --quiet "$monitor" || die "Discord Gateway monitor not active; inspect source connectivity first"
  echo "===== VERIFY CORRECT DISCORD BOT / CHANNEL (READ-ONLY) ====="
  runuser -u "$user" -- env HOME="$home" PYTHONPATH=/opt/sktrader "$python" - "$token" "$channel" "$bot_author" <<'PY'
import sys
from pathlib import Path
from sktrader.discord_limit_bridge import bot_profile
key=Path(sys.argv[1]).read_text(encoding="utf-8").strip()
actual=bot_profile(key,sys.argv[2])
if actual!=sys.argv[3]:
    raise SystemExit("Unexpected Discord bot account; aborting live feed")
print("Verified Discord bot:",actual,"channel:",sys.argv[2])
PY
  # A new cutoff + unique LIVE database excludes every previous monitor test.
  cutoff_ms=$(date -u +%s%3N)
  [[ "$cutoff_ms" =~ ^[0-9]{13}$ ]] || die "Cannot establish UTC cutoff"
  cat > "$unit" <<UNIT
[Unit]
Description=DK Capital verified Discord limit LIVE input feed to isolated MT5 (broker orders only by separately enabled EA)
Wants=network-online.target
After=network-online.target
StartLimitIntervalSec=120
StartLimitBurst=5

[Service]
Type=simple
User=dkvnc
Group=dkvnc
WorkingDirectory=/opt/sktrader
Environment=HOME=/var/lib/dkcapital-desktop
Environment=PYTHONUNBUFFERED=1
Environment=PYTHONPATH=/opt/sktrader
ExecStart=/var/lib/dkcapital-desktop/.venv-discord-limit/bin/python -u -m sktrader.discord_limit_bridge --channel-id 1565527578163552327 --token-file /var/lib/dkcapital-desktop/.local/share/sktrader/discord-bot-token --database /var/lib/dkcapital-desktop/.local/share/sktrader/discord-limit-live.db --output /var/lib/dkcapital-desktop/.wine-mt5/drive_c/users/dkvnc/AppData/Roaming/MetaQuotes/Terminal/Common/Files/discord_limit_30m_signals.csv --author-id 1554225452091048017 --author-id 508740193369587722 --not-before-ms $cutoff_ms
Restart=on-failure
RestartSec=10
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
  echo "STAGED LIVE FEED SERVICE ONLY; NOT STARTED."
  echo "Cutover epoch (ms): $cutoff_ms"
  echo "Pinned authors: DK Capital bot $bot_author and Danny $human_author"
  echo "Output when started: $feed"
  echo "The EA controls actual broker orders with EnableNewEntries and global Algo Trading."
  echo "Start feed: sudo bash deploy/live-discord-limit-vnc5906.sh start"
  exit 0
fi

# start: only an EXACT expected isolated service can route signals to MT5.
[[ -f "$unit" ]] || die "Live service not staged. Run stage first"
grep -Fq "User=dkvnc" "$unit" || die "Unexpected live service user"
grep -Fq -- "--author-id $bot_author --author-id $human_author" "$unit" || die "Trusted-author allowlist mismatch"
grep -Fq -- "--not-before-ms " "$unit" || die "Missing historical-message cutoff"
grep -Fq -- "--output $feed" "$unit" || die "Live signal target mismatch"
grep -Fq -- "--database $db" "$unit" || die "Dedicated fresh live database mismatch"
[[ ! -e "$db" && ! -L "$db" ]] || die "Live DB exists. Refusing to reinitialize; use systemctl to inspect existing service"
[[ ! -e "$feed" && ! -L "$feed" ]] || die "Signal file already exists. Refusing to clobber an existing feed"
systemctl is-active --quiet "$legacy" && die "Legacy actionable Discord feed active"
check_account
echo "===== START LIVE DISCORD-TO-MT5 SIGNAL FEED ====="
systemctl enable --now "$service"
sleep 2
systemctl is-active --quiet "$service" || {
  journalctl -u "$service" -n 40 --no-pager
  die "Live Discord feed is not active"
}
[[ -f "$feed" && "$(stat -c %u "$feed")" == "$(id -u "$user")" ]] || die "Live signal file missing or wrong owner"
echo "LIVE SIGNAL FILE CONNECTED: $feed"
echo "No historical monitor test was copied or replayed."
echo "Keep EA EnableNewEntries=false until live feed logs and risk rules are verified."
echo "Monitor: sudo journalctl -u $service -n 40 --no-pager"
echo "IMPORTANT: if the EA is ALREADY armed, future eligible signals can place broker orders immediately."
