#!/usr/bin/env bash
set -euo pipefail
AUTH=/var/lib/dkcapital-mt5-3/.local/share/sktrader/Xauthority-155
SOCKET=/tmp/.X11-unix/X155
if [[ ! -f "$AUTH" ]]; then
  echo "Missing existing private JIFU Xauthority; refusing to create new credentials" >&2
  exit 1
fi
# Existing Xvfb started manually is preserved; never kill or replace it.
if [[ -S "$SOCKET" ]]; then
  echo "Reusing existing isolated Xvfb display :155"
  while [[ -S "$SOCKET" ]]; do sleep 3; done
  exit 1
fi
# During a VPS reboot, recreate only JIFU's dedicated Xvfb display.
exec /usr/bin/Xvfb :155 -screen 0 1920x1080x24 -auth "$AUTH" -nolisten tcp
