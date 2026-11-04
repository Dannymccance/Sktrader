#!/usr/bin/env bash
# Compile and STAGE a NEW, independent XAUUSD channel-limit DEMO EA.
# Does not attach the EA, enable Algo Trading, restart MT5, or touch GWS/AUROUX.
set -Eeuo pipefail

[[ "$(id -u)" == 0 ]] || { echo "Run with sudo" >&2; exit 1; }
source=/opt/sktrader/mt5/DiscordChannelLimit30m.mq5
demo_home=/var/lib/dkcapital-mt5-3
demo_experts="$demo_home/.wine/drive_c/Program Files/MetaTrader 5/MQL5/Experts"
common="$demo_home/.wine/drive_c/users/dkmt5c/AppData/Roaming/MetaQuotes/Terminal/Common/Files"
account="$common/sktrader_demo_account.csv"
target="$demo_experts/DiscordChannelLimit30m.ex5"
[[ -s "$source" && -d "$demo_experts" && -s "$account" ]] || {
    echo "No approved source, demo Experts folder or broker telemetry" >&2; exit 1;
}
[[ ! -e "$target" ]] || {
    echo "Candidate EA already staged. Will not overwrite an existing EA." >&2; exit 1;
}

python3 - "$account" <<'PY'
import csv,sys,time
with open(sys.argv[1], encoding="utf-8-sig", newline="") as f:
    rows=list(csv.DictReader(f, delimiter=";"))
if len(rows)!=1: raise SystemExit("Ambiguous demo account publisher")
a=rows[0]
expected={"login":"5058169035","mode":"DEMO","currency":"GBP","server":"MetaQuotes-Demo"}
if any(a.get(k)!=v for k,v in expected.items()):
    raise SystemExit("Wrong MT5 account; refusing installation")
try: age=time.time()-int(a["generated_at"])
except (KeyError,ValueError): raise SystemExit("Malformed broker timestamp")
if not 0<=age<=120: raise SystemExit("Broker account telemetry stale")
print("Verified MetaQuotes-Demo GBP account 5058169035")
PY

container=dkcapital-mt5-compiler-vnc
root=/opt/dkcapital-mt5-compiler/workspace
experts="$root/MetaTrader 5/MQL5/Experts"
alias="$root/AurouxMetaEditor"
[[ "$(docker inspect --format '{{.State.Running}}' "$container" 2>/dev/null || true)" == true ]] \
    || { echo "Isolated MetaEditor container is not running" >&2; exit 1; }
mount=$(docker inspect --format '{{range .Mounts}}{{if eq .Destination "/workspace"}}{{.Source}}{{end}}{{end}}' "$container")
[[ "$mount" == "$root" ]] || { echo "Compiler mount mismatch" >&2; exit 1; }
[[ "$(docker exec "$container" id -u)" == 11050 ]] \
    || { echo "Compiler container UID mismatch" >&2; exit 1; }
[[ -s "$root/MetaTrader 5/MetaEditor64.exe" && -d "$experts" ]] \
    || { echo "Isolated MetaEditor/Experts missing" >&2; exit 1; }
if [[ ! -L "$alias" ]]; then
    [[ ! -e "$alias" ]] || { echo "Compiler alias conflict" >&2; exit 1; }
    ln -s 'MetaTrader 5' "$alias"
fi
[[ "$(readlink "$alias")" == 'MetaTrader 5' ]] \
    || { echo "Invalid compiler alias" >&2; exit 1; }

stage=$(mktemp -d "$experts/DiscordLimitBuild.XXXXXXXX")
chmod 0700 "$stage"
chown 11050:11050 "$stage"
candidate="$stage/DiscordChannelLimit30m.mq5"
install -o 11050 -g 11050 -m 0644 "$source" "$candidate"
cmp -s "$source" "$candidate" || { echo "Source parity failed" >&2; exit 1; }
stage_name=$(basename "$stage")
win_source='Z:\workspace\AurouxMetaEditor\MQL5\Experts\'"$stage_name"'\DiscordChannelLimit30m.mq5'
win_include='Z:\workspace\AurouxMetaEditor\MQL5'
cli_prefix=/home/compiler/.wine-discord-limit-cli

echo "Compiling channel-limit EA in isolated MetaEditor..."
timeout 150 docker exec -e WINEPREFIX="$cli_prefix" "$container" wineboot -u \
    > "$stage/wineboot.stdout" 2>&1 || {
        tail -35 "$stage/wineboot.stdout" >&2; exit 1;
    }
resolved=$(timeout 20 docker exec -e WINEPREFIX="$cli_prefix" "$container" \
    winepath -u "$win_source" | tr -d '\r')
[[ -n "$resolved" ]] && docker exec "$container" test -s "$resolved" || {
    echo "Compiler cannot resolve source path. Diagnostics: $stage" >&2; exit 1;
}
if ! timeout 180 docker exec -e WINEPREFIX="$cli_prefix" \
    -w /workspace/AurouxMetaEditor "$container" \
    wine ./MetaEditor64.exe "/compile:$win_source" "/include:$win_include" /log \
    > "$stage/compile.stdout" 2>&1; then
    tail -50 "$stage/compile.stdout" >&2
    echo "Compiler diagnostics: $stage" >&2
    exit 1
fi

for ((i=0;i<12;i++)); do
    [[ -s "$stage/DiscordChannelLimit30m.ex5" && -s "$stage/DiscordChannelLimit30m.log" ]] && break
    sleep 1
done
[[ -s "$stage/DiscordChannelLimit30m.ex5" && -s "$stage/DiscordChannelLimit30m.log" ]] || {
    echo "MetaEditor did not produce both EX5 and compile log" >&2
    tail -50 "$stage/compile.stdout" >&2 || true
    ls -lah "$stage" >&2
    echo "Compiler diagnostics: $stage" >&2
    exit 1
}
python3 - "$stage/DiscordChannelLimit30m.log" <<'PY'
import pathlib,re,sys
raw=pathlib.Path(sys.argv[1]).read_bytes()
if raw.startswith((b"\xff\xfe",b"\xfe\xff")): log=raw.decode("utf-16",errors="replace")
elif raw.count(b"\0")>len(raw)//8: log=raw.decode("utf-16-le",errors="replace")
else: log=raw.decode("utf-8",errors="replace")
print(log[-1500:])
if not re.search(r"\b0\s+errors?\b",log,re.I):
    raise SystemExit("Compiler reported errors or no verifiable success")
PY
binary="$stage/DiscordChannelLimit30m.ex5"
[[ -s "$binary" && "$binary" -nt "$candidate" ]] || {
    echo "Stale or missing compiled EA" >&2; exit 1;
}
install -o dkmt5c -g dkmt5c -m 0644 "$candidate" "$demo_experts/DiscordChannelLimit30m.mq5"
install -o dkmt5c -g dkmt5c -m 0644 "$binary" "$target"
cmp -s "$binary" "$target" || { echo "Binary transfer mismatch" >&2; exit 1; }
sha256sum "$source" "$binary" "$target"
echo "STAGED new DEMO EA at $target"
echo "Not attached or activated. MT5 must have one XAUUSD chart with the EA loaded and Algo Trading on."
echo "No existing MT5 service, GWS EA or AUROUX EA was restarted or modified."
