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

# Use the user's existing dedicated MetaEditor VNC 5903 container.
# Its /workspace bind mount is isolated from all broker Wine prefixes.
compiler_container=dkcapital-mt5-compiler-vnc
compiler_root=/opt/dkcapital-mt5-compiler/workspace
compiler_install="$compiler_root/MetaTrader 5"
compiler_experts="$compiler_install/MQL5/Experts"
compiler_editor="$compiler_install/MetaEditor64.exe"
command -v docker >/dev/null || die "Docker not found; isolated MetaEditor unavailable"
[[ "$(docker inspect --format '{{.State.Running}}' "$compiler_container" 2>/dev/null || true)" == true ]] \
    || die "Existing isolated MetaEditor VNC 5903 container is not running"
compiler_mount=$(docker inspect --format '{{range .Mounts}}{{if eq .Destination "/workspace"}}{{.Source}}{{end}}{{end}}' "$compiler_container")
[[ "$compiler_mount" == "$compiler_root" ]] \
    || die "Compiler /workspace mount differs from the known isolated compiler workspace"
[[ -f "$compiler_editor" && -d "$compiler_experts" && -d "$compiler_install/MQL5/Include/Trade" ]] \
    || die "Isolated MetaEditor64.exe or standard MQL5 headers are missing"
[[ "$(docker exec "$compiler_container" id -u)" == 11050 ]] \
    || die "Isolated compiler container is not running as restricted compiler UID 11050"

# MetaEditor under Wine can silently ignore /compile when Windows paths
# contain spaces. Point to the exact same isolated installation via a
# no-space alias. This is only a symlink in the compiler workspace.
compiler_alias="$compiler_root/AurouxMetaEditor"
if [[ ! -L "$compiler_alias" ]]; then
    [[ ! -e "$compiler_alias" ]] || die "Compiler path alias exists but is not a symlink"
    ln -s 'MetaTrader 5' "$compiler_alias"
fi
[[ "$(readlink "$compiler_alias")" == 'MetaTrader 5' ]] \
    || die "Compiler path alias does not point to the approved isolated installation"

# Every candidate is built in a unique, compiler-only directory. Other
# DKCAPITAL/GWS compiler sources and artifacts are never touched.
stage=$(mktemp -d "$compiler_experts/AurouxDemoCompile.XXXXXXXX")
chown 11050:11050 "$stage"
chmod 0700 "$stage"
# Retain compiler output when something fails; remove after successful cutover.
retain_stage=1
cleanup_stage() {
    if [[ "$retain_stage" == 0 ]]; then
        rm -rf -- "$stage"
    else
        echo "Compiler diagnostic files retained: $stage" >&2
    fi
}
trap cleanup_stage EXIT
install -o 11050 -g 11050 -m 0644 "$source_mq5" "$stage/AurouxFollower.mq5"
cmp -s "$source_mq5" "$stage/AurouxFollower.mq5" \
    || die "Isolated compiler source differs from reviewed AUROUX source"
stage_name=$(basename "$stage")
win_source='Z:\workspace\AurouxMetaEditor\MQL5\Experts\'"$stage_name"'\AurouxFollower.mq5'

echo "Compiling AUROUX with separate command-line Wine profile inside the isolated container..."
# Avoid forwarding /compile to the existing MetaEditor GUI session.
cli_prefix=/home/compiler/.wine-auroux-cli
win_include='Z:\workspace\AurouxMetaEditor\MQL5'
timeout 150 docker exec -e WINEPREFIX="$cli_prefix" "$compiler_container" wineboot -u \
    || die "Isolated AUROUX Wine compiler profile could not initialize"

# Check that Wine resolves the exact candidate source, not just a valid-looking path.
win_source_unix=$(timeout 20 docker exec -e WINEPREFIX="$cli_prefix" \
    "$compiler_container" winepath -u "$win_source" | tr -d '\r') \
    || die "Wine could not translate the isolated AUROUX source path"
[[ -n "$win_source_unix" ]] || die "Wine returned an empty source path"
docker exec "$compiler_container" test -s "$win_source_unix" \
    || die "Wine's translated AUROUX source path does not exist: $win_source_unix"
echo "Wine resolved isolated compiler source: $win_source_unix"

if ! timeout 180 docker exec -e WINEPREFIX="$cli_prefix" \
    -w '/workspace/AurouxMetaEditor' "$compiler_container" \
    wine ./MetaEditor64.exe "/compile:$win_source" "/include:$win_include" /log \
    > "$stage/compile.stdout" 2>&1; then
    tail -45 "$stage/compile.stdout" >&2 || true
    die "Isolated MetaEditor command failed or timed out"
fi

# A completed Wine command may leave the compiler flushing its output.
for ((i=0; i<12; i++)); do
    [[ -s "$stage/AurouxFollower.log" && -s "$stage/AurouxFollower.ex5" ]] && break
    sleep 1
done
if [[ ! -s "$stage/AurouxFollower.log" ]]; then
    echo "===== Compiler output =====" >&2
    tail -65 "$stage/compile.stdout" >&2 || true
    echo "===== Compiler candidate files =====" >&2
    ls -la "$stage" >&2 || true
    die "No compilation log generated; compiler files retained for investigation"
fi

python3 - "$stage/AurouxFollower.log" <<'PY'
import pathlib,re,sys
p=pathlib.Path(sys.argv[1])
if not p.is_file():
    raise SystemExit("Compilation log missing from isolated MetaEditor")
raw=p.read_bytes()
if raw.startswith((b'\xff\xfe',b'\xfe\xff')):
    log=raw.decode("utf-16",errors="replace")
elif raw.count(b"\0")>len(raw)//8:
    log=raw.decode("utf-16-le",errors="replace")
else:
    log=raw.decode("utf-8",errors="replace")
print("Isolated MetaEditor result:\n"+log[-1600:])
if not re.search(r'\b0\s+errors?\b',log,re.I):
    raise SystemExit("Isolated MetaEditor did not report 0 errors")
PY
compiled="$stage/AurouxFollower.ex5"
[[ -s "$compiled" && "$compiled" -nt "$stage/AurouxFollower.mq5" ]] \
    || die "Isolated compiler did not produce a fresh AUROUX EX5"
[[ "$(stat -c '%s' "$compiled")" -gt 30000 ]] \
    || die "Compiled AUROUX EX5 is unexpectedly small; refusing installation"
echo "Verified isolated AUROUX EX5:"
sha256sum "$source_mq5" "$compiled"

# Back up ONLY the demo AUROUX EA, preserving its exact identity and
# terminal-global event checkpoint. Never edit/restart the GWS EA.
backup=$(mktemp -d /tmp/auroux-demo-repair.XXXXXXXX)
chmod 0700 "$backup"
cp -p "$target_mq5" "$backup/AurouxFollower.mq5"
cp -p "$target_ex5" "$backup/AurouxFollower.ex5"
completed=0
bridge_restarted=0
restore_on_error() {
    result=$?
    if [[ $completed != 1 ]]; then
        if [[ $bridge_restarted == 1 ]]; then
            # After bridge cutover, reverting to a six-field EA would
            # create a protocol mismatch. Stop bridge rather than trade.
            systemctl stop "$service" || true
            echo "Bridge cutover validation failed: bridge stopped; AUROUX EA not rolled back automatically." >&2
        else
            cp -p "$backup/AurouxFollower.mq5" "$target_mq5" || true
            cp -p "$backup/AurouxFollower.ex5" "$target_ex5" || true
            chown "$owner:$owner" "$target_mq5" "$target_ex5" || true
            echo "AUROUX EA restored from backup; original bridge process kept running." >&2
        fi
        echo "AUROUX backup retained at $backup" >&2
    else
        rm -rf -- "$backup"
    fi
    cleanup_stage
}
trap restore_on_error EXIT

echo "Installing freshly compiled AUROUX-only EA into verified DEMO MT5..."
install -o "$owner" -g "$owner" -m 0644 "$source_mq5" "$target_mq5"
install -o "$owner" -g "$owner" -m 0644 "$compiled" "$target_ex5"
cmp -s "$compiled" "$target_ex5" || die "Installed AUROUX EA differs from isolated compilation"
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
bridge_restarted=1
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
retain_stage=0
echo "SUCCESS: AUROUX demo EA 1.11 and bridge are aligned and verified. GWS services unchanged."
