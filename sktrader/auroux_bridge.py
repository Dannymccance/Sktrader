"""Export already-collected AUROUX signals to MT5's Terminal/Common/Files.

Example:
 python -m sktrader.auroux_bridge --database /var/lib/dkcapital-mt5-3/.local/share/sktrader/signals.db \
   --output '/path/to/MetaQuotes/Terminal/Common/Files/auroux_signals.csv' --follow

This runs separately from the existing watch-only collector. It does not trade.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
from datetime import datetime, timezone
import sqlite3
import time
from pathlib import Path


def price_field(fields: list[dict], name: str) -> float:
    candidates = [x.get("value", "").strip() for x in fields
                  if x.get("name", "").replace(" ", "").lower() == name.replace(" ", "").lower()]
    if len(candidates) != 1:
        raise ValueError(f"missing or ambiguous {name}")
    import re
    # A single standalone numeric level only. Reject ranges and commentary.
    if not re.fullmatch(r"[+-]?\d+(?:\.\d+)?", candidates[0]):
        raise ValueError(f"{name} must be one numeric price")
    value = float(candidates[0])
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"{name} invalid")
    return value


def deadline_epoch(signal: dict) -> int | None:
    """Fail closed when an original time cannot be established.

    A displayed minute age is rounded down, so conservatively assume the
    oldest moment compatible with that display, not a fresh 180s window.
    """
    observed = signal.get("observed_at")
    age = str(signal.get("relative_age", "")).strip().lower()
    try:
        observed_epoch = datetime.fromisoformat(observed.replace("Z", "+00:00")).timestamp()
    except (AttributeError, ValueError, TypeError):
        return None
    if age == "just now":
        elapsed = 59
    else:
        match = re.fullmatch(r"(\\d+|a|an) (second|minute|hour|day|week)s? ago", age)
        if not match:
            return None
        quantity = 1 if match.group(1) in ("a", "an") else int(match.group(1))
        units = {"second": 1, "minute": 60, "hour": 3600, "day": 86400, "week": 604800}
        seconds = units[match.group(2)]
        elapsed = quantity * seconds + (seconds - 1)
    return int(observed_epoch - elapsed + 180)


def export_lines(connection: sqlite3.Connection) -> list[str]:
    rows = connection.execute("SELECT id,payload FROM scanner_events ORDER BY id").fetchall()
    result: list[str] = []
    for identifier, raw in rows:
        signal = json.loads(raw)
        if signal.get("scanner") != "AUROUX" or signal.get("symbol") != "XAUUSD":
            continue
        try:
            fields = signal["fields"]
            side = signal["side"]
            if side not in ("BUY", "SELL"):
                raise ValueError("invalid direction")
            poi = price_field(fields, "Point of Interest")
            sl = price_field(fields, "Invalidation")
            tp = price_field(fields, "Exit1")
            deadline = deadline_epoch(signal)
            if deadline is None:
                raise ValueError("unknown issue age")
            if not ((sl < poi < tp) if side == "BUY" else (tp < poi < sl)):
                raise ValueError("invalid price structure")
        except (KeyError, TypeError, ValueError) as exc:
            # Never silently substitute missing levels.
            print(f"Skipping AUROUX event {identifier}: {exc}", flush=True)
            continue
        result.append(f"{int(identifier)};{side};{poi:.8f};{sl:.8f};{tp:.8f}\n")
    return result


def export_once(db_path: Path, output: Path) -> int:
    # Read-only connection; writer is the existing Sktrader collector.
    uri = f"file:{db_path.resolve().as_posix()}?mode=ro"
    with sqlite3.connect(uri, uri=True, timeout=10) as con:
        lines = export_lines(con)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(output.name + ".tmp")
    with temporary.open("w", encoding="ascii", newline="") as f:
        f.writelines(lines)
        f.flush()
        os.fsync(f.fileno())
    os.replace(temporary, output)
    return len(lines)


def latest_event_id(db_path: Path) -> int:
    """Cheap change detection so unchanged feeds do not rewrite the MT5 file."""
    uri = f"file:{db_path.resolve().as_posix()}?mode=ro"
    with sqlite3.connect(uri, uri=True, timeout=1) as con:
        row = con.execute("SELECT COALESCE(MAX(id),0) FROM scanner_events").fetchone()
    return int(row[0])


def main() -> None:
    parser = argparse.ArgumentParser(description="AUROUX saved-event-to-MT5 bridge")
    parser.add_argument("--database", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--follow", action="store_true")
    parser.add_argument("--interval", type=float, default=0.1)
    args = parser.parse_args()
    if args.interval < 0.05:
        parser.error("--interval must be >= 0.05")
    previous_id = -1
    while True:
        newest = latest_event_id(args.database)
        if newest != previous_id or not args.output.is_file():
            count = export_once(args.database, args.output)
            previous_id = newest
            print(f"AUROUX bridge refreshed {count} signals, scanner event watermark {newest}", flush=True)
        if not args.follow:
            return
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
