#!/usr/bin/env bash
# Read-only archive from two independently persisted signal stores.
set -euo pipefail
if [[ "${EUID}" -ne 0 ]]; then
  echo "Run via sudo/root to access the existing private signal stores" >&2
  exit 1
fi
OUT="/opt/sktrader/exports/$(date -u +%Y%m%dT%H%M%SZ)"
install -d -o dkmt5c -g dkmt5c -m 0700 "$OUT"
cd /opt/sktrader
runuser -u dkmt5c -- /opt/sktrader/.venv/bin/python -m sktrader.export_signals \
  --database /var/lib/dkcapital-mt5-3/.local/share/sktrader/signals.db \
  --output-dir "$OUT"
cd /opt/dkcapital
docker compose --profile discord run --rm --no-deps \
  -v "$OUT:/exports" discord-bot \
  python -m dkcapital.export_signals --output-dir /exports
chmod 0700 "$OUT"
chmod 0600 "$OUT"/*.csv "$OUT"/*.html
cd /opt/sktrader/exports
tar -czf "$OUT.tar.gz" "$(basename "$OUT")"
chmod 0600 "$OUT.tar.gz"
echo "Export ready: $OUT.tar.gz"
echo "Download from your PC with: scp root@31.187.72.71:$OUT.tar.gz ."
