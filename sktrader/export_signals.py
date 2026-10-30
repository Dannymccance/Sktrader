"""Offline exports of durable Sktrader XAUUSD scanner records. Read-only."""
from __future__ import annotations
import argparse
import csv
import html
import json
import sqlite3
from pathlib import Path


def records(database: Path) -> list[dict[str, str]]:
    conn = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    try:
        rows = conn.execute(
            "SELECT id, observed_at, payload, delivered_at FROM scanner_events ORDER BY id"
        ).fetchall()
    finally:
        conn.close()
    result = []
    for event_id, observed, payload, delivered in rows:
        data = json.loads(payload)
        fields = data.get("fields") or []
        details = {str(f.get("name", "")): str(f.get("value", "")) for f in fields}
        result.append({
            "source_system": "JIFU", "source": str(data.get("scanner") or ""),
            "event_id": str(event_id), "source_timestamp": str(data.get("relative_age") or ""),
            "captured_at": str(data.get("observed_at") or observed),
            "delivered_at": str(delivered or ""), "symbol": str(data.get("symbol") or ""),
            "timeframe": str(data.get("timeframe") or ""),
            "direction": str(data.get("side") or ""),
            "entry": details.get("Point of Interest", ""),
            "stop_loss": details.get("Invalidation", ""),
            "take_profits": "; ".join(f"{key}: {value}" for key, value in details.items() if key.lower().startswith("exit")),
            "details_json": json.dumps(fields, ensure_ascii=False, sort_keys=True),
        })
    return result


def write_exports(rows: list[dict[str, str]], out: Path, stem: str = "jifu-signals") -> tuple[Path, Path]:
    out.mkdir(parents=True, exist_ok=True, mode=0o700)
    keys = ["source_system", "source", "event_id", "source_timestamp", "captured_at",
            "delivered_at", "symbol", "timeframe", "direction", "entry",
            "stop_loss", "take_profits", "details_json"]
    csv_path, html_path = out / f"{stem}.csv", out / f"{stem}.html"
    with csv_path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=keys, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    headers = "".join(f"<th>{html.escape(k)}</th>" for k in keys)
    body = "".join("<tr>" + "".join(f"<td>{html.escape(str(row.get(k, '')))}</td>" for k in keys) + "</tr>" for row in rows)
    page = ("<!doctype html><html lang='en'><meta charset='utf-8'><title>JIFU Signal Archive</title>"
            "<style>body{font:14px system-ui;margin:2rem;color:#171717}table{border-collapse:collapse;width:100%}"
            "td,th{border:1px solid #ddd;padding:8px;text-align:left;vertical-align:top}"
            "th{background:#eee;position:sticky;top:0}td{overflow-wrap:anywhere}tr:nth-child(even){background:#fafafa}"
            "</style><h1>JIFU XAUUSD Signal Archive</h1><p>Exported stored events only. No claim of complete source history.</p>"
            f"<p>{len(rows)} recorded signals</p><table><thead><tr>{headers}</tr></thead><tbody>{body}</tbody></table></html>")
    html_path.write_text(page, encoding="utf-8")
    csv_path.chmod(0o600)
    html_path.chmod(0o600)
    return csv_path, html_path


def main() -> None:
    parser = argparse.ArgumentParser(description="Read-only JIFU SQLite signal export")
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    csv_path, html_path = write_exports(records(args.database), args.output_dir)
    print(f"Exported: {csv_path} and {html_path}")


if __name__ == "__main__":
    main()
