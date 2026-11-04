#!/usr/bin/env bash
# Discover recent Discord message authors and safely pin the NON-TRADING monitor to one author.
# No EA, broker setting, signal output or existing JIFU service is modified.
set -Eeuo pipefail
umask 077
cmd="${1:-inspect}"
[[ "$cmd" == inspect || "$cmd" == set ]] || { echo "Usage: sudo bash deploy/configure-discord-monitor-author.sh {inspect|set AUTHOR_ID}" >&2; exit 2; }
[[ $(id -u) == 0 ]] || { echo "Run as root using sudo" >&2; exit 1; }
uid=dkvnc
home=/var/lib/dkcapital-desktop
channel=1565527578163552327
token="$home/.local/share/sktrader/discord-bot-token"
unit=/etc/systemd/system/sktrader-discord-limit-monitor.service
monitor_service=sktrader-discord-limit-monitor.service
dir=/etc/systemd/system/sktrader-discord-limit-monitor.service.d
override="$dir/author.conf"
die() { echo "ERROR: $*" >&2; exit 1; }
[[ -s "$token" ]] || die "Private Discord token missing; run setup-discord-limit-vnc5906.sh prepare-monitor first"
[[ "$(stat -c %u "$token")" == "$(id -u "$uid")" ]] || die "Unexpected private token owner"
[[ -s "$unit" ]] || die "Monitor unit missing"
grep -Fq "User=dkvnc" "$unit" || die "Not the expected isolated monitor"
grep -Fq -- "--channel-id 1565527578163552327" "$unit" || die "Unexpected Discord channel"
grep -Fq -- "--output /var/lib/dkcapital-desktop/.local/share/sktrader/discord-limit-monitor.csv" "$unit" || die "Monitor would write outside isolated output"
grep -Fq "ExecStart=/var/lib/dkcapital-desktop/.venv-discord-limit/bin/python" "$unit" || die "Unknown monitor Python runtime"

if [[ "$cmd" == inspect ]]; then
  echo "===== RECENT MESSAGES IN DISCORD SOURCE CHANNEL (AUTHOR METADATA ONLY) ====="
  runuser -u "$uid" -- env HOME="$home" python3 - "$token" "$channel" <<'PY'
import json,sys,urllib.error,urllib.request
from pathlib import Path
token=Path(sys.argv[1]).read_text(encoding="utf-8").strip()
channel=sys.argv[2]
url=f"https://discord.com/api/v10/channels/{channel}/messages?limit=15"
req=urllib.request.Request(url,headers={"Authorization":"Bot "+token,"User-Agent":"dkcapital-limit-author-inspect/1.0"})
try:
    with urllib.request.urlopen(req,timeout=15) as resp:
        messages=json.load(resp)
except urllib.error.HTTPError as exc:
    raise SystemExit(f"Discord read-message-history request failed: HTTP {exc.code}. Enable Read Message History, or copy the author ID in Discord Developer Mode.") from None
if not isinstance(messages,list):
    raise SystemExit("Unexpected Discord message list format")
if not messages:
    print("No recent messages. Use Discord Developer Mode to copy the test message author ID.")
for msg in messages:
    author=msg.get("author") or {}
    name=str(author.get("global_name") or author.get("username") or "unknown").replace("\n"," ")[:45]
    print("message_id="+str(msg.get("id",""))+" author_id="+str(author.get("id",""))+" name="+name+" bot="+str(bool(author.get("bot",False)))+" time="+str(msg.get("timestamp","")))
print("Compare the author_id of the test message from Danny. Token and message contents were not printed.")
PY
  echo "===== MONITOR SERVICE (NOT A TRADE INPUT) ====="
  systemctl is-active "$monitor_service" || true
  if [[ -f "$override" ]]; then sed -n "/^ExecStart=/p" "$override" | sed -E "s/ --token-file [^ ]+/ --token-file [private-token-path]/"; else echo "Using default bot-only author"; fi
  exit 0
fi

author="${2:-}"
[[ "$author" =~ ^[0-9]{15,22}$ ]] || die "Provide the exact Discord author ID copied from the matching test message"
[[ ! -e "$override" && ! -L "$override" ]] || die "Author override already exists; refusing to overwrite it"
systemctl is-active --quiet "$monitor_service" || die "Non-trading monitor is not active"
if [[ -d "$dir" ]]; then
  [[ "$(stat -c %u "$dir")" == 0 ]] || die "Unexpected monitor override directory owner"
fi
install -d -o root -g root -m 0755 "$dir"
cat > "$override" <<EOF
[Service]
ExecStart=
ExecStart=/var/lib/dkcapital-desktop/.venv-discord-limit/bin/python -u -m sktrader.discord_limit_bridge --channel-id 1565527578163552327 --token-file /var/lib/dkcapital-desktop/.local/share/sktrader/discord-bot-token --database /var/lib/dkcapital-desktop/.local/share/sktrader/discord-limit-monitor.db --output /var/lib/dkcapital-desktop/.local/share/sktrader/discord-limit-monitor.csv --author-id $author
EOF
chmod 0644 "$override"
systemctl daemon-reload
systemctl restart "$monitor_service"
systemctl is-active --quiet "$monitor_service" || { journalctl -u "$monitor_service" -n 30 --no-pager; die "Monitor restart failed"; }
echo "MONITOR-ONLY Discord author pinned to $author. No actionable MT5 signal output changed."
echo "Monitor may require MESSAGE_CONTENT privileged intent because selected author differs from the bot."
echo "Check: sudo journalctl -u $monitor_service -n 35 --no-pager"
echo "Do not enable MT5 Algo Trading or EnableNewEntries based only on a connected gateway."
