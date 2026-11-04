#!/usr/bin/env bash
# Compile ONLY the combined limit+telemetry EA in the separate MetaEditor VNC.
# Does not install or activate any broker EA, restart MT5, or touch GWS/AUROUX.
set -Eeuo pipefail
[[ "$(id -u)" == 0 ]] || { echo "Run with sudo" >&2; exit 1; }
source=/opt/sktrader/mt5/DiscordLimitUnified.mq5
[[ -s "$source" ]] || { echo "Unified EA source missing" >&2; exit 1; }

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

stage=$(mktemp -d "$experts/UnifiedLimitBuild.XXXXXXXX")
chmod 0700 "$stage"
chown 11050:11050 "$stage"
candidate="$stage/DiscordLimitUnified.mq5"
install -o 11050 -g 11050 -m 0644 "$source" "$candidate"
cmp -s "$source" "$candidate" || { echo "Source parity failed" >&2; exit 1; }
stage_name=$(basename "$stage")
win_source='Z:\workspace\AurouxMetaEditor\MQL5\Experts\'"$stage_name"'\DiscordLimitUnified.mq5'
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
    [[ -s "$stage/DiscordLimitUnified.ex5" && -s "$stage/DiscordLimitUnified.log" ]] && break
    sleep 1
done
[[ -s "$stage/DiscordLimitUnified.ex5" && -s "$stage/DiscordLimitUnified.log" ]] || {
    echo "MetaEditor did not produce both EX5 and compile log" >&2
    if [[ -s "$stage/DiscordLimitUnified.log" ]]; then
        echo "===== ACTUAL METAEDITOR COMPILER ERRORS =====" >&2
        python3 - "$stage/DiscordLimitUnified.log" <<'PY'
import pathlib,sys
raw=pathlib.Path(sys.argv[1]).read_bytes()
if raw.startswith((b"\xff\xfe",b"\xfe\xff")): log=raw.decode("utf-16",errors="replace")
elif raw.count(b"\0")>len(raw)//8: log=raw.decode("utf-16-le",errors="replace")
else: log=raw.decode("utf-8",errors="replace")
for line in log.splitlines()[-100:]:
    print(line)
PY
    fi
    echo "===== WINE COMPILER OUTPUT =====" >&2
    tail -50 "$stage/compile.stdout" >&2 || true
    ls -lah "$stage" >&2
    echo "Compiler diagnostics: $stage" >&2
    exit 1
}
python3 - "$stage/DiscordLimitUnified.log" <<'PY'
import pathlib,re,sys
raw=pathlib.Path(sys.argv[1]).read_bytes()
if raw.startswith((b"\xff\xfe",b"\xfe\xff")): log=raw.decode("utf-16",errors="replace")
elif raw.count(b"\0")>len(raw)//8: log=raw.decode("utf-16-le",errors="replace")
else: log=raw.decode("utf-8",errors="replace")
print(log[-1500:])
if not re.search(r"\b0\s+errors?\b",log,re.I):
    raise SystemExit("Compiler reported errors or no verifiable success")
PY
binary="$stage/DiscordLimitUnified.ex5"
[[ -s "$binary" && "$binary" -nt "$candidate" ]] || {
    echo "Stale or missing compiled EA" >&2; exit 1;
}
artifact_dir=/opt/dkcapital-mt5-compiler/artifacts
install -d -m 0750 "$artifact_dir"
git_id=$(git -C /opt/sktrader rev-parse --short=12 HEAD)
output="$artifact_dir/DiscordLimitUnified_"${git_id}".ex5"
[[ ! -e "$output" ]] || {
    echo "Artifact exists, refusing overwrite: $output" >&2; exit 1;
}
install -m 0640 "$binary" "$output"
cmp -s "$binary" "$output" || { echo "Compiled EX5 checksum mismatch" >&2; exit 1; }
sha256sum "$source" "$binary" "$output"
echo "COMPILED artifact only: $output"
echo "No MT5 terminal was changed, no EA was attached, and no orders were placed."
