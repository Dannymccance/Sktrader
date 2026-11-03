#!/usr/bin/env bash
# One-command AUROUX DEMO bridge + EA repair. Never touches the GWS REAL EA.
set -Eeuo pipefail

repo=/opt/sktrader
owner=dkmt5c
home=/var/lib/dkcapital-mt5-3
prefix="$home/.wine"
install_dir="$prefix/drive_c/Program Files/MetaTrader 5"
experts="$install_dir/MQL5/Experts"
source_mq5="$repo/mt5/AurouxFollower.mq5"
target_mq5="$experts/AurouxFollower.mq5"
target_ex5="$experts/AurouxFollower.ex5"
common="$prefix/drive_c/users/dkmt5c/AppData/Roaming/MetaQuotes/Terminal/Common/Files"
account="$common/sktrader_demo_account.csv"
health="$common/auroux_follower_health.csv"
csv="$common/auroux_signals.csv"
service=sktrader-auroux-bridge.service

die() { echo "ERROR: $*" >&2; exit 1; }
[[ $(id -u) == 0 ]] || die "Run with sudo bash deploy/repair-auroux-demo.sh"
[[ -f "$source_mq5" && -d "$experts" ]] || die "Expected AUROUX source or installed MT5 Experts folder missing"
[[ -f "$target_ex5" ]] || die "Existing AUROUX EA binary not found; will not guess a new installation target"
[[ -f "$account" ]] || die "No demo broker account snapshot; refusing to alter a possibly REAL terminal"
systemctl is-active --quiet "$service" || die "AUROUX bridge is not running"
[[ -f "$csv" ]] || die "AUROUX bridge output CSV not found"

# The publisher is broker-originated, not a user-entered account description.
python3 - "$account" <<'PY'
import csv, sys, time
with open(sys.argv[1], encoding="utf-8-sig", newline="") as f:
    rows=list(csv.DictReader(f,delimiter=";"))
if len(rows)!=1:
    raise SystemExit("Refusing deployment: broker account telemetry absent or ambiguous")
a=rows[0]
expected={"login":"5058169035","mode":"DEMO","currency":"GBP","server":"MetaQuotes-Demo"}
if any(a.get(k)!=v for k,v in expected.items()):
    raise SystemExit("Refusing deployment: terminal is not the approved MetaQuotes DEMO GBP account")
try:
    age=time.time()-int(a["generated_at"])
except (ValueError, KeyError):
    raise SystemExit("Refusing deployment: invalid broker snapshot timestamp")
if not 0<=age<=120:
    raise SystemExit("Refusing deployment: broker account telemetry is stale")
print("Approved broker identity: MetaQuotes-Demo GBP, login 5058169035 (DEMO)")
PY

editor="$install_dir/metaeditor64.exe"
[[ -f "$editor" ]] || editor="$install_dir/metaeditor.exe"
[[ -f "$editor" ]] || die "MetaEditor executable not found inside expected MT5 installation"
wine_bin=$(command -v wine || command -v wine64 || true)
[[ -n "$wine_bin" ]] || die "Wine executable unavailable"
[[ -f "$home/.local/share/sktrader/Xauthority-155" ]] || die "Sktrader's existing virtual display authority not found"

backup=$(mktemp -d /tmp/auroux-demo-repair.XXXXXXXX)
chmod 700 "$backup"
cp -p "$target_mq5" "$backup/AurouxFollower.mq5"
cp -p "$target_ex5" "$backup/AurouxFollower.ex5"

completed=0
restore_on_error() {
    result=$?
    if [[ $completed != 1 ]]; then
        cp -p "$backup/AurouxFollower.mq5" "$target_mq5" || true
        cp -p "$backup/AurouxFollower.ex5" "$target_ex5" || true
        chown "$owner:$owner" "$target_mq5" "$target_ex5" || true
        echo "AUROUX EA files restored from backup; existing bridge process was not restarted." >&2
    fi
    if [[ $result -eq 0 && $completed -eq 1 ]]; then
        rm -rf -- "$backup"
    else
        echo "Backup retained at $backup" >&2
    fi
}
trap restore_on_error EXIT

echo "Installing approved demo-only AUROUX EA source..."
install -o "$owner" -g "$owner" -m 0644 "$source_mq5" "$target_mq5"
rm -f "$experts/AurouxFollower.log"
win_source='C:\Program Files\MetaTrader 5\MQL5\Experts\AurouxFollower.mq5'

echo "Compiling the AUROUX EA through MetaEditor, no UI interaction required..."
timeout 120 runuser -u "$owner" -- env \
    HOME="$home" WINEPREFIX="$prefix" DISPLAY=:155 \
    XAUTHORITY="$home/.local/share/sktrader/Xauthority-155" \
    WINEDEBUG=-all "$wine_bin" "$editor" "/compile:$win_source" /log \
    || die "MetaEditor command failed or timed out"

python3 - "$experts/AurouxFollower.log" <<'PY'
import pathlib,re,sys
p=pathlib.Path(sys.argv[1])
if not p.is_file():
    raise SystemExit("Compilation log missing; refusing to assume successful compilation")
raw=p.read_bytes()
if raw.startswith((b'\xff\xfe',b'\xfe\xff')):
    log=raw.decode("utf-16",errors="replace")
elif raw.count(b"\0")>len(raw)//8:
    log=raw.decode("utf-16-le",errors="replace")
else:
    log=raw.decode("utf-8",errors="replace")
print("MetaEditor compilation result:\n"+log[-1600:])
if not re.search(r'\b0\s+errors?\b',log,re.I):
    raise SystemExit("MetaEditor did not report zero compilation errors")
PY
[[ -s "$target_ex5" && "$target_ex5" -nt "$backup/AurouxFollower.ex5" ]] \
    || die "New compiled AurouxFollower.ex5 not produced"
echo "Compiled EA updated. Checking that the MT5 demo terminal actually loaded it..."

# Recompiling an attached EA reinitializes it. This heartbeat is written
# by the EA itself and verifies the exact demo account, build, and permissions.
ready=0
for ((i=0;i<25;i++)); do
    if python3 - "$health" <<'PY'
import csv,sys,time
try:
    with open(sys.argv[1],encoding="utf-8-sig",newline="") as f:
        rows=list(csv.DictReader(f,delimiter=";"))
    if len(rows)!=1: raise ValueError("no heartbeat")
    a=rows[0]
    assert a["build"]=="1.11" and a["login"]=="5058169035"
    assert a["mode"]=="DEMO" and a["symbol"] in ("XAUUSD","XAUUSD+")
    assert 0<=time.time()-int(a["generated_at"])<=12
    assert a["trade_allowed"]=="1"
    print("MT5 confirmed: AUROUX 1.11 running on demo, Algo Trading enabled, checkpoint="+a["checkpoint"])
except (OSError,KeyError,ValueError,AssertionError):
    sys.exit(1)
PY
    then ready=1; break; fi
    sleep 1
done
[[ "$ready" == 1 ]] || die "No fresh trading-enabled AUROUX EA heartbeat. It may not be attached to an XAUUSD chart, or Algo Trading is disabled. Not switching CSV formats."

echo "Reconnecting seven-field MARKET signal bridge..."
systemctl restart "$service"
sleep 3
systemctl is-active --quiet "$service" || die "AUROUX bridge did not restart correctly"
python3 - "$csv" <<'PY'
import sys,pathlib
p=pathlib.Path(sys.argv[1])
rows=[line.split(";") for line in p.read_text(encoding="ascii").splitlines() if line.strip()]
if not rows or any(len(x)!=7 or x[-1] not in ("MARKET","BUY_LIMIT","SELL_LIMIT","BUY_STOP","SELL_STOP") for x in rows):
    raise SystemExit("AUROUX bridge output is not in the expected seven-field format")
print("Bridge operational:",len(rows),"validated seven-field signal rows")
print("Existing signals remain subject to their original expiry and checkpoint; no replay requested.")
PY
completed=1
echo "SUCCESS: AUROUX demo EA 1.11 and bridge are aligned and verified. GWS services unchanged."
