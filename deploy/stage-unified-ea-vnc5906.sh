#!/usr/bin/env bash
# Stage the verified, DISARMED unified Discord EA into the isolated 5906 MT5.
# Does not attach, enable Algo Trading, restart a service or change account data.
set -Eeuo pipefail
umask 077

action="${1:-inspect}"
[[ "$action" == inspect || "$action" == stage ]] || {
  echo "Usage: sudo bash deploy/stage-unified-ea-vnc5906.sh {inspect|stage}" >&2; exit 2;
}
[[ $(id -u) -eq 0 ]] || { echo "Run with sudo on VPS SSH" >&2; exit 1; }

home=/var/lib/dkcapital-desktop
prefix="$home/.wine-mt5"
program="$prefix/drive_c/Program Files/MetaTrader 5"
user=dkvnc
source=/opt/sktrader/mt5/DiscordLimitUnified.mq5
artifact=/opt/dkcapital-mt5-compiler/artifacts/DiscordLimitUnified_d4385f2a89df.ex5
source_sha=1905a6017590ba8b19a0975bee0ebfbf2ed5044e0879e5cf1cf760b58d38c8c3
artifact_sha=1295fa33add8fbe406500373133781ae0c7f2be12908327985a8e7adefa3ac95
unit=/etc/systemd/system/dkcapital-desktop-vnc.service
die() { echo "ERROR: $*" >&2; exit 1; }

[[ -f "$unit" ]] || die "Dedicated VNC unit missing"
grep -Fq 'User=dkvnc' "$unit" || die "Wrong VNC desktop user"
grep -Fq -- '-rfbport 5906 -localhost yes' "$unit" || die "Wrong VNC port or public binding"
systemctl is-active --quiet dkcapital-desktop-vnc.service ||
  die "The dedicated VNC 5906 desktop is not active"
[[ "$(getent passwd "$user" | cut -d: -f6)" == "$home" ]] ||
  die "Unexpected desktop home"
[[ -s "$program/terminal64.exe" ]] || die "This dedicated MT5 terminal was not found"
[[ -s "$source" && -s "$artifact" ]] || die "Compiled artifact or source missing"

actual_source=$(sha256sum "$source" | cut -d' ' -f1)
actual_artifact=$(sha256sum "$artifact" | cut -d' ' -f1)
[[ "$actual_source" == "$source_sha" ]] ||
  die "EA source has changed since the verified compile; rerun compiler and review artifact"
[[ "$actual_artifact" == "$artifact_sha" ]] ||
  die "Compiled artifact hash differs from the verified MetaEditor build"

# The launcher uses standard MT5 mode (no /portable). Under Wine, standard
# data folders live in the user's Roaming/MetaQuotes/Terminal/<instance>/MQL5.
# Never install into Program Files by guessing: MT5 may not read that copy.
roaming="$prefix/drive_c/users/$user/AppData/Roaming/MetaQuotes/Terminal"
[[ -d "$roaming" ]] ||
  die "Roaming terminal data folder missing. In MT5 choose File > Open Data Folder"
mapfile -d '' -t experts_dirs < <(
  find "$roaming" -mindepth 3 -maxdepth 4 -type d \
    -path "$roaming/*/MQL5/Experts" -print0
)

if (( ${#experts_dirs[@]} != 1 )); then
  echo "Discovered MT5 Experts folders: ${#experts_dirs[@]}"
  if (( ${#experts_dirs[@]} )); then printf '  %s\n' "${experts_dirs[@]}"; fi
  die "Cannot identify exactly one MT5 data folder; use File > Open Data Folder in MT5"
fi
experts="${experts_dirs[0]}"
resolved_prefix=$(realpath -e "$prefix")
resolved_experts=$(realpath -e "$experts")
case "$resolved_experts/" in
  "$resolved_prefix/"*) ;;
  *) die "Experts directory resolves outside the isolated MT5 Wine prefix" ;;
esac
[[ "$(stat -c %u "$experts")" == "$(id -u "$user")" ]] ||
  die "Experts folder is not owned by the isolated VNC user"
target="$experts/DiscordLimitUnified.ex5"
echo "Selected MT5 Experts: $experts"
echo "Verified EA source SHA256: $actual_source"
echo "Verified compiled EX5 SHA256: $actual_artifact"
if [[ "$action" == inspect ]]; then
  if [[ -e "$target" ]]; then
    echo "Existing target: $target"
    sha256sum "$target"
  else
    echo "Not yet staged: $target"
  fi
  echo "Read-only inspect done. No MT5 service or EA changed."
  exit 0
fi

if [[ -e "$target" ]]; then
  cmp -s "$artifact" "$target" && {
    echo "Already staged: $target (matching verified SHA256)"
    exit 0
  }
  die "An EA with this name already exists and differs; refusing overwrite"
fi

tmp=$(mktemp "$experts/.DiscordLimitUnified.XXXXXXXX.ex5")
trap 'rm -f -- "$tmp"' EXIT
install -o "$user" -g "$user" -m 0644 "$artifact" "$tmp"
cmp -s "$artifact" "$tmp" || die "Staged binary checksum mismatch"
# Same-filesystem hardlink atomically creates the target without replacing it.
ln -- "$tmp" "$target" || die "Cannot stage EA without overwriting existing target"
cmp -s "$artifact" "$target" || die "Installed EX5 checksum mismatch"
echo "STAGED (NOT ATTACHED): $target"
echo "MT5 Navigator > Expert Advisors > Refresh, then attach DiscordLimitUnified."
echo "Keep EnableNewEntries=false. Do not enable Algo Trading yet."
echo "No broker account, existing GWS/AUROUX, compiler or JIFU service was changed."
