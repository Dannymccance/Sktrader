"""Pure alert parsing, snapshot reconciliation and durable outbox for Sktrader."""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

PRICE = r"([0-9][0-9,]*(?:\.[0-9]+)?)"
SIDE = re.compile(r"\b(1M|5M|15M|30M|1H|4H)\s*-\s*(BUY|SELL)\b", re.I)
RELATIVE = re.compile(r"\b(\d+)\s+(second|minute|hour|day)s?\s+ago\b", re.I)

# JIFU's All feed contains multiple scanners and symbols. We track those
# cards for ordered overlap, but only parse AUROUX XAUUSD into notifications.
CARD_AGE = re.compile(
    r"\b(?:(?:\d+|a|an)\s+(?:second|minute|hour|day|week)s?\s+ago|just\s+now)\b",
    re.I,
)


def feed_fingerprint(text: str) -> str:
    """Stable identity for any scanner card as its display age advances."""
    compact = re.sub(r"\s+", " ", CARD_AGE.sub("", text)).strip()
    if not compact or not re.search(r"\bPoint of Interest\b", compact, re.I):
        raise SnapshotError("Invalid scanner card")
    return hashlib.sha256(compact.encode("utf-8")).hexdigest()


def auroux_xauusd_card(text: str) -> bool:
    """Source and symbol must match; never forward WOLFX/XAUUSD as Auroux."""
    return bool(re.match(r"\s*AUROUX\b", text, re.I)
                and re.search(r"\bXAUUSD\b", text, re.I))



class AlertError(ValueError):
    """Unsafe or malformed scanner alert."""


class SnapshotError(RuntimeError):
    """No trustworthy overlap with the previously observed alert list."""


def _field(text: str, label: str) -> Decimal:
    match = re.search(r"\b" + label + r"\s*:?\s*" + PRICE, text, re.I)
    if not match:
        raise AlertError(f"Missing {label}")
    try:
        value = Decimal(match.group(1).replace(",", ""))
        if not value.is_finite() or value <= 0:
            raise AlertError(f"Invalid {label}")
        return value
    except InvalidOperation as exc:
        raise AlertError(f"Invalid {label}") from exc


@dataclass(frozen=True)
class Alert:
    scanner: str
    symbol: str
    timeframe: str
    side: str
    poi: str
    invalidation: str
    tp1: str
    tp2: str
    level: int
    relative_age: str

    def fingerprint(self) -> str:
        """Relative age intentionally excluded because it changes every minute."""
        data = asdict(self)
        data.pop("relative_age")
        canonical = json.dumps(data, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode()).hexdigest()


def parse_card(text: str) -> Alert:
    """Parse one complete, visible alert card, never the entire sidebar."""
    compact = re.sub(r"\s+", " ", text.strip())
    if not re.search(r"\bAUROUX\b", compact, re.I):
        raise AlertError("Not an Auroux card")
    if not re.search(r"\bXAUUSD\b", compact, re.I):
        raise AlertError("Not an XAUUSD card")
    side = SIDE.search(compact)
    if not side:
        raise AlertError("Timeframe and direction missing")
    level_match = re.search(r"\bLevel\s*:?\s*(\d+)\s*pips?\b", compact, re.I)
    if not level_match:
        raise AlertError("Scanner level missing")
    poi = _field(compact, r"Point\s+of\s+Interest")
    stop = _field(compact, r"Invalidation")
    tp1 = _field(compact, r"Exit\s*1")
    tp2 = _field(compact, r"Exit\s*2")
    direction = side.group(2).upper()
    if direction == "BUY" and not (stop < poi < tp1 < tp2):
        raise AlertError("Invalid BUY level ordering")
    if direction == "SELL" and not (tp2 < tp1 < poi < stop):
        raise AlertError("Invalid SELL level ordering")
    age = RELATIVE.search(compact)
    return Alert(
        scanner="AUROUX", symbol="XAUUSD", timeframe=side.group(1).upper(),
        side=direction, poi=str(poi), invalidation=str(stop),
        tp1=str(tp1), tp2=str(tp2), level=int(level_match.group(1)),
        relative_age=age.group(0) if age else "unavailable",
    )


def new_prefix(previous: list[str], current: list[str]) -> list[int]:
    """Return indices of new cards at the top; fail closed if no overlap.

    Entries are newest first. Repeated prices can be legitimate new events;
    matching the old sequence (rather than a set of hashes) preserves them.
    Without stable source IDs, identity is still imperfect if the site
    reorders, replaces or truncates too many cards between checks.
    """
    if not current:
        raise SnapshotError("No alert cards found")
    if not previous or current == previous:
        return []
    options = []
    for offset in range(len(current)):
        overlap = 0
        while (overlap < len(previous) and offset + overlap < len(current)
               and current[offset + overlap] == previous[overlap]):
            overlap += 1
        if overlap:
            options.append((overlap, offset))
    if not options:
        raise SnapshotError("Alert list changed without an identifiable overlap")
    length, offset = max(options)
    if len(previous) >= 2 and length < 2:
        raise SnapshotError("Insufficient overlap to identify new alerts safely")
    return list(range(offset))


class EventStore:
    def __init__(self, filename: Path):
        filename.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(filename))
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("""CREATE TABLE IF NOT EXISTS snapshot (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            fingerprints TEXT NOT NULL
        )""")
        self.conn.execute("""CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            observed_at TEXT NOT NULL,
            payload TEXT NOT NULL,
            delivered_at TEXT
        )""")
        # Separate from the earlier Auroux-only snapshot. The first mixed
        # feed read creates a fresh baseline and never replays old cards.
        self.conn.execute("""CREATE TABLE IF NOT EXISTS feed_snapshot (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            fingerprints TEXT NOT NULL
        )""")
        self.conn.commit()

    def observe(self, alerts: list[Alert]) -> tuple[int, bool]:
        """Atomically commit new outbox events and updated snapshot.

        On first startup, baseline historical cards without notifying.
        """
        current = [a.fingerprint() for a in alerts]
        row = self.conn.execute("SELECT fingerprints FROM snapshot WHERE id=1").fetchone()
        previous = json.loads(row[0]) if row else None
        indices = new_prefix(previous or [], current)
        now = datetime.now(timezone.utc).isoformat()
        with self.conn:
            if previous is not None:
                # Oldest to newest for notification order.
                for index in reversed(indices):
                    self.conn.execute(
                        "INSERT INTO events(observed_at,payload) VALUES (?,?)",
                        (now, json.dumps(asdict(alerts[index]), sort_keys=True)),
                    )
            self.conn.execute(
                "INSERT INTO snapshot(id,fingerprints) VALUES(1,?) "
                "ON CONFLICT(id) DO UPDATE SET fingerprints=excluded.fingerprints",
                (json.dumps(current),),
            )
        return (len(indices) if previous is not None else 0, previous is None)

    def observe_feed(self, cards: list[str]) -> tuple[int, bool]:
        """Track every scanner card, but queue new AUROUX XAUUSD alerts only.

        A mixed JIFU feed can display zero Auroux alerts. Non-target entries
        provide sequence overlap so the next Auroux alert is not missed.
        The first read baselines history, and malformed eligible cards fail
        closed before committing any new snapshot.
        """
        if not cards:
            raise SnapshotError("Empty JIFU alerts feed")
        current = [feed_fingerprint(card) for card in cards]
        row = self.conn.execute(
            "SELECT fingerprints FROM feed_snapshot WHERE id=1"
        ).fetchone()
        previous = json.loads(row[0]) if row else None
        indices = new_prefix(previous, current) if previous is not None else []
        eligible = []
        for index in reversed(indices):
            if not auroux_xauusd_card(cards[index]):
                continue
            try:
                eligible.append(parse_card(cards[index]))
            except AlertError as exc:
                raise SnapshotError(f"Unparseable new AUROUX XAUUSD card: {exc}") from exc
        now = datetime.now(timezone.utc).isoformat()
        with self.conn:
            for alert in eligible:
                self.conn.execute(
                    "INSERT INTO events(observed_at,payload) VALUES (?,?)",
                    (now, json.dumps(asdict(alert), sort_keys=True)),
                )
            self.conn.execute(
                "INSERT INTO feed_snapshot(id,fingerprints) VALUES(1,?) "
                "ON CONFLICT(id) DO UPDATE SET fingerprints=excluded.fingerprints",
                (json.dumps(current),),
            )
        return len(eligible), previous is None

    def pending(self) -> list[tuple[int, str, Alert]]:
        rows = self.conn.execute(
            "SELECT id, observed_at, payload FROM events WHERE delivered_at IS NULL ORDER BY id"
        ).fetchall()
        return [(id_, observed, Alert(**json.loads(raw))) for id_, observed, raw in rows]

    def delivered(self, event_id: int) -> None:
        with self.conn:
            self.conn.execute(
                "UPDATE events SET delivered_at=? WHERE id=? AND delivered_at IS NULL",
                (datetime.now(timezone.utc).isoformat(), event_id),
            )
