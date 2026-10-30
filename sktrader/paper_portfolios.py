"""Offline, read-only, tick-driven paper portfolios for five JIFU scanners.

No MT5, HTTP, Discord or trading dependencies. This code can NEVER submit orders.
Real bid/ask quotes must be ingested by a separate, authenticated data pipeline.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_DOWN
import json
from pathlib import Path
import re
import sqlite3

SCANNERS = ("SNIPER PRO", "NEXUS", "HARMONICS", "PRIME", "WOLF X")
START_USD = Decimal("1000")
RISK = Decimal("0.04")
MAX_SIGNAL_AGE_SECONDS = 180


def number(value):
    try:
        result = Decimal(str(value))
        if not result.is_finite() or result <= 0:
            raise ValueError("nonpositive/nonfinite price")
        return result
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ValueError("invalid numeric value") from exc


def timestamp(value):
    dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if dt.tzinfo is None:
        raise ValueError("timezone is required")
    return dt.astimezone(timezone.utc)


def exact_field(fields, name):
    matches = [str(f.get("value", "")).strip() for f in fields
               if re.sub(r"\s+", "", str(f.get("name", ""))).lower()
               == re.sub(r"\s+", "", name).lower()]
    if len(matches) != 1 or not re.fullmatch(r"\d+(?:\.\d+)?", matches[0]):
        raise ValueError("missing/ambiguous " + name)
    return number(matches[0])


def validate_signal(signal):
    scanner = signal.get("scanner")
    if scanner not in SCANNERS or signal.get("symbol") != "XAUUSD":
        raise ValueError("scanner or symbol not enabled")
    side = signal.get("side")
    if side not in ("BUY", "SELL"):
        raise ValueError("invalid side")
    fields = signal.get("fields")
    if not isinstance(fields, list):
        raise ValueError("fields missing")
    entry = exact_field(fields, "Point of Interest")
    stop = exact_field(fields, "Invalidation")
    tp = exact_field(fields, "Exit1")
    if not ((stop < entry < tp) if side == "BUY" else (tp < entry < stop)):
        raise ValueError("invalid SL/TP geometry")
    return scanner, side, entry, stop, tp


class PaperPortfolios:
    """Deterministic simulator driven by externally collected quotes.

    Conservative assumptions: one position per scanner, no price interpolation,
    no missed-tick reconstruction, TP1 only, no commission approximation.
    Positions are not opened without an actual bid/ask crossing and a known
    broker contract size. Reported paper P&L is gross until fees are configured.
    """
    def __init__(self, database: Path, *, contract_size=100, volume_step="0.01",
                 min_volume="0.01", max_volume="100", commission_per_lot="0"):
        self.contract_size = number(contract_size)
        self.step = number(volume_step)
        self.min_volume = number(min_volume)
        self.max_volume = number(max_volume)
        self.commission = Decimal(str(commission_per_lot))
        if self.commission < 0 or not self.commission.is_finite():
            raise ValueError("invalid commission")
        database.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(database)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS portfolios (
          scanner TEXT PRIMARY KEY, balance TEXT NOT NULL, realized TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS paper_signals (
          event_id INTEGER PRIMARY KEY, scanner TEXT NOT NULL, side TEXT,
          poi TEXT, sl TEXT, tp TEXT, expiry TEXT, state TEXT NOT NULL,
          reason TEXT, entry_time TEXT, entry_price TEXT, lots TEXT,
          exit_time TEXT, exit_price TEXT, net_pnl TEXT, observed_at TEXT);
        CREATE TABLE IF NOT EXISTS quotes (
          seq INTEGER PRIMARY KEY AUTOINCREMENT, stamp TEXT NOT NULL,
          bid TEXT NOT NULL, ask TEXT NOT NULL);
        """)
        if "observed_at" not in [x[1] for x in self.db.execute("PRAGMA table_info(paper_signals)")]:
            self.db.execute("ALTER TABLE paper_signals ADD COLUMN observed_at TEXT")
        for scanner in SCANNERS:
            self.db.execute("INSERT OR IGNORE INTO portfolios VALUES (?,?,?)",
                            (scanner, str(START_USD), "0"))
        self.db.commit()

    def snapshot(self):
        result = {}
        for scanner, balance, realized in self.db.execute(
                "SELECT scanner,balance,realized FROM portfolios ORDER BY scanner"):
            open_count = self.db.execute(
                "SELECT COUNT(*) FROM paper_signals WHERE scanner=? AND state='OPEN'",
                (scanner,)).fetchone()[0]
            result[scanner] = {"balance_usd": balance, "realized_usd": realized,
                               "open_positions": open_count}
        return result

    def accept(self, event_id: int, signal: dict, *, expires_at: str):
        if type(event_id) is not int or event_id < 1:
            raise ValueError("invalid event id")
        scanner = signal.get("scanner")
        if scanner not in SCANNERS:
            return "EXCLUDED"
        now = timestamp(signal["observed_at"])
        expiry = timestamp(expires_at)
        try:
            source, side, poi, sl, tp = validate_signal(signal)
            if expiry <= now or (expiry-now).total_seconds() > MAX_SIGNAL_AGE_SECONDS:
                raise ValueError("expired/unsafe publication window")
            values = (source, side, str(poi), str(sl), str(tp), expiry.isoformat(),
                      "PENDING", None)
        except (ValueError, KeyError, TypeError) as exc:
            values = (scanner, None, None, None, None, None, "SKIPPED", str(exc)[:200])
        with self.db:
            self.db.execute(
                "INSERT OR IGNORE INTO paper_signals "
                "(event_id,scanner,side,poi,sl,tp,expiry,state,reason,observed_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)", (event_id, *values, now.isoformat()))
        return self.db.execute("SELECT state FROM paper_signals WHERE event_id=?",
                               (event_id,)).fetchone()[0]

    def on_quote(self, *, observed_at: str, bid, ask):
        """Process quote exactly once by timestamp and sequence from provider.

        Caller must enforce ordered, gap-monitored delivery. No price-bar
        simulation is used because OHLC cannot establish SL/TP ordering.
        """
        stamp = timestamp(observed_at)
        b, a = number(bid), number(ask)
        if a < b:
            raise ValueError("ask below bid")
        # Reject out-of-order quotes rather than backdating fills.
        latest = self.db.execute("SELECT stamp FROM quotes ORDER BY seq DESC LIMIT 1").fetchone()
        if latest and stamp <= timestamp(latest[0]):
            raise ValueError("duplicate or out-of-order quote")
        with self.db:
            self.db.execute("INSERT INTO quotes(stamp,bid,ask) VALUES (?,?,?)",
                            (stamp.isoformat(), str(b), str(a)))
            # Exit existing positions BEFORE evaluating new entries.
            positions = self.db.execute(
                "SELECT event_id,scanner,side,sl,tp,lots,entry_price "
                "FROM paper_signals WHERE state='OPEN' ORDER BY event_id").fetchall()
            for event_id, scanner, side, sl, tp, lots, entry in positions:
                sl, tp, lots, entry = map(Decimal, (sl, tp, lots, entry))
                exit_quote = b if side == "BUY" else a
                hit_sl = exit_quote <= sl if side == "BUY" else exit_quote >= sl
                hit_tp = exit_quote >= tp if side == "BUY" else exit_quote <= tp
                if not (hit_sl or hit_tp):
                    continue
                # Worse of observed quote and stop level on adverse gaps.
                price = (min(sl, exit_quote) if side == "BUY" else max(sl, exit_quote)) if hit_sl else tp
                raw = (price-entry if side == "BUY" else entry-price)*lots*self.contract_size
                net = raw - lots*self.commission
                self.db.execute(
                    "UPDATE paper_signals SET state=?,exit_time=?,exit_price=?,net_pnl=? WHERE event_id=?",
                    ("SL" if hit_sl else "TP1", stamp.isoformat(), str(price), str(net), event_id))
                self.db.execute("UPDATE portfolios SET balance=CAST(balance AS REAL)+?,"
                                "realized=CAST(realized AS REAL)+? WHERE scanner=?",
                                (float(net), float(net), scanner))
            pending = self.db.execute(
                "SELECT event_id,scanner,side,poi,sl,tp,expiry,observed_at FROM paper_signals "
                "WHERE state='PENDING' ORDER BY event_id").fetchall()
            for event_id, scanner, side, poi, sl, tp, expiry, signal_time in pending:
                if signal_time and stamp < timestamp(signal_time):
                    continue
                if stamp >= timestamp(expiry):
                    self.db.execute("UPDATE paper_signals SET state='EXPIRED' WHERE event_id=?",
                                    (event_id,))
                    continue
                if self.db.execute("SELECT 1 FROM paper_signals WHERE scanner=? AND state='OPEN'",
                                   (scanner,)).fetchone():
                    continue
                poi, sl = Decimal(poi), Decimal(sl)
                execution = a if side == "BUY" else b
                # Conservative marketable-at-POI approach. Never fill at a
                # better price than observed bid/ask.
                if (execution > poi if side == "BUY" else execution < poi):
                    continue
                if (execution <= sl if side == "BUY" else execution >= sl):
                    self.db.execute("UPDATE paper_signals SET state='SKIPPED',"
                                    "reason='quote crossed stop before entry' WHERE event_id=?",
                                    (event_id,))
                    continue
                balance = Decimal(self.db.execute(
                    "SELECT balance FROM portfolios WHERE scanner=?", (scanner,)).fetchone()[0])
                per_lot_loss = abs(execution-sl)*self.contract_size + self.commission
                volume = ((balance*RISK/per_lot_loss)/self.step).to_integral_value(
                    rounding=ROUND_DOWN)*self.step
                if volume < self.min_volume or volume > self.max_volume:
                    self.db.execute("UPDATE paper_signals SET state='SKIPPED',"
                                    "reason='volume outside broker constraints' WHERE event_id=?",
                                    (event_id,))
                    continue
                self.db.execute(
                    "UPDATE paper_signals SET state='OPEN',entry_time=?,entry_price=?,lots=? "
                    "WHERE event_id=?", (stamp.isoformat(), str(execution), str(volume), event_id))
        return self.snapshot()
