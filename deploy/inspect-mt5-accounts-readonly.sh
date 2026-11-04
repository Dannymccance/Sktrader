#!/usr/bin/env bash
# Read-only VPS discovery. No MT5 account, service, or broker changes.
set -euo pipefail
echo "===== MT5 SYSTEMD SERVICES ====="
systemctl list-units --all --type=service --no-pager --plain | grep -Ei 'dkcapital-mt5|sktrader' || true
echo "===== BROKER-ORIGIN ACCOUNT IDENTITIES (IF AVAILABLE) ====="
python3 - <<'PY'
import csv
import time
from pathlib import Path

found=0
for home in sorted(Path("/var/lib").glob("dkcapital-mt5*")):
    root=home/".wine/drive_c/users"
    for common in root.glob("*/AppData/Roaming/MetaQuotes/Terminal/Common/Files"):
        for name in ("sktrader_demo_account.csv","discord_limit_account.csv"):
            f=common/name
            if not f.is_file(): continue
            try:
                with f.open(encoding="utf-8-sig",newline="") as stream:
                    rows=list(csv.DictReader(stream,delimiter=";"))
                if len(rows)!=1: raise ValueError("expected one row")
                row=rows[0]
                age=int(time.time())-int(row["generated_at"])
                print("DATA:",common)
                print("FILE:",name,"LOGIN:",row.get("login"),
                      "MODE:",row.get("mode"),"CURRENCY:",row.get("currency"),
                      "SERVER:",row.get("server"),"AGE_SECONDS:",age)
                found+=1
            except (OSError,ValueError,KeyError) as exc:
                print("Unreadable broker identity:",f,type(exc).__name__)
if found==0:
    print("No current broker-account CSV detected. This does not prove no REAL account is running.")
else:
    print("Only use fresh snapshots matching the exact running terminal account.")
PY
echo "===== MT5 INSTALLATIONS (FILES ONLY) ====="
for dir in /var/lib/dkcapital-mt5*/.wine/drive_c/Program\ Files/MetaTrader\ 5/MQL5/Experts; do
    if [[ -d "$dir" ]]; then printf '%s\n' "$dir"; fi
done
echo "Read-only check complete. No programs were changed."
