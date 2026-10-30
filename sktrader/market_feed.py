"""Read-only ingestion of MT5 common-file market exports into SQLite.

Does not send orders, connect to MT5 trade APIs, or publish Discord messages.
Uses complete CSV snapshots, unique tick identity and durable transactional writes.
"""
from __future__ import annotations
import argparse
import csv
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
import sqlite3
import time


def decimal(value):
    try:
        x = Decimal(value)
        if not x.is_finite() or x <= 0:
            raise ValueError("nonpositive")
        return x
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ValueError("invalid market numeric field") from exc


def read_csv(path, columns):
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        table = csv.DictReader(f, delimiter=";")
        if table.fieldnames != columns:
            raise ValueError(f"unexpected CSV schema {path.name}")
        return list(table)


class MarketFeed:
    def __init__(self, db: Path, *, expected_login: int, max_age_seconds=120):
        if expected_login <= 0:
            raise ValueError("expected demo login required")
        self.login = expected_login
        self.max_age = max_age_seconds
        db.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(db)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS feed_ticks (
          time_msc INTEGER NOT NULL, bid TEXT NOT NULL, ask TEXT NOT NULL,
          flags INTEGER NOT NULL,
          PRIMARY KEY(time_msc,bid,ask,flags));
        CREATE TABLE IF NOT EXISTS feed_m1 (
          time INTEGER PRIMARY KEY, open TEXT NOT NULL, high TEXT NOT NULL,
          low TEXT NOT NULL, close TEXT NOT NULL,
          tick_volume INTEGER NOT NULL, spread INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS feed_identity (
          id INTEGER PRIMARY KEY CHECK(id=1), login INTEGER NOT NULL,
          mode TEXT NOT NULL, currency TEXT NOT NULL, server TEXT NOT NULL,
          symbol TEXT NOT NULL, contract_size TEXT NOT NULL,
          tick_size TEXT NOT NULL, tick_value TEXT NOT NULL,
          volume_min TEXT NOT NULL, volume_max TEXT NOT NULL,
          volume_step TEXT NOT NULL, generated_at INTEGER NOT NULL);
        """)
        self.db.commit()

    def ingest(self, common: Path, *, now=None):
        now = time.time() if now is None else now
        rows = read_csv(common/"sktrader_xau_identity.csv", [
            "login","mode","currency","server","symbol","contract_size",
            "tick_size","tick_value","volume_min","volume_max","volume_step",
            "generated_at"])
        if len(rows) != 1:
            raise ValueError("invalid broker identity")
        identity = rows[0]
        if (int(identity["login"]) != self.login or identity["mode"] != "DEMO"
                or identity["currency"] not in ("USD", "GBP")
                or not identity["server"]
                or not identity["symbol"].startswith("XAUUSD")):
            raise ValueError("broker profile mismatch")
        generated = int(identity["generated_at"])
        if not 0 <= now-generated <= self.max_age:
            raise ValueError("stale/future broker data")
        for field in ("contract_size","tick_size","tick_value","volume_min",
                      "volume_max","volume_step"):
            decimal(identity[field])

        # A fresh account heartbeat does not imply a fresh executable quote.
        # Quarantining stale snapshots also prevents old ticks from being
        # replayed on service restart after the market closes.
        quote_path = common/"sktrader_xau_quotes.csv"
        quote_age = now - quote_path.stat().st_mtime
        if quote_age < -15 or quote_age > 30:
            raise ValueError("market closed or quote export stale; no paper fills")
        ticks = read_csv(common/"sktrader_xau_quotes.csv",
                         ["time_msc","bid","ask","flags"])
        bars = read_csv(common/"sktrader_xau_m1.csv",
                         ["time","open","high","low","close","tick_volume","spread"])
        with self.db:
            self.db.execute(
                "INSERT OR REPLACE INTO feed_identity VALUES (1,?,?,?,?,?,?,?,?,?,?,?,?)",
                (self.login, identity["mode"], identity["currency"], identity["server"],
                 identity["symbol"],*[identity[k] for k in (
                     "contract_size","tick_size","tick_value","volume_min",
                     "volume_max","volume_step")],generated))
            for row in ticks:
                stamp = int(row["time_msc"])
                bid, ask = decimal(row["bid"]), decimal(row["ask"])
                flags = int(row["flags"])
                if stamp <= 0 or stamp > (now+15)*1000 or ask < bid or flags < 0:
                    raise ValueError("invalid/future broker tick; UTC reconciliation required")
                self.db.execute("INSERT OR IGNORE INTO feed_ticks VALUES (?,?,?,?)",
                                (stamp,str(bid),str(ask),flags))
            for row in bars:
                stamp = int(row["time"])
                o,h,l,c = [decimal(row[k]) for k in ("open","high","low","close")]
                volume, spread = int(row["tick_volume"]),int(row["spread"])
                if (stamp <= 0 or stamp > now or stamp % 60 != 0 or
                    l > min(o,c) or h < max(o,c) or h < l or volume < 0 or spread < 0):
                    raise ValueError("invalid M1 bar")
                self.db.execute("INSERT OR IGNORE INTO feed_m1 VALUES (?,?,?,?,?,?,?)",
                                (stamp,str(o),str(h),str(l),str(c),volume,spread))
        return {"ticks":self.db.execute("SELECT COUNT(*) FROM feed_ticks").fetchone()[0],
                "m1":self.db.execute("SELECT COUNT(*) FROM feed_m1").fetchone()[0]}


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--common",type=Path,required=True)
    p.add_argument("--database",type=Path,required=True)
    p.add_argument("--expected-login",type=int,required=True)
    p.add_argument("--follow",action="store_true")
    p.add_argument("--interval",type=float,default=1.0)
    args=p.parse_args()
    if args.interval < 0.1:
        p.error("interval must be >= 0.1")
    feed=MarketFeed(args.database,expected_login=args.expected_login)
    while True:
        try:
            print(feed.ingest(args.common),flush=True)
        except (OSError,ValueError,sqlite3.Error) as exc:
            print(f"market ingest rejected: {exc}",flush=True)
            if not args.follow:
                raise
        if not args.follow:
            break
        time.sleep(args.interval)


if __name__=="__main__":
    main()
