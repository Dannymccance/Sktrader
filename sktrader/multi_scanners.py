"""Watch-only XAUUSD signals from every JIFU scanner with independent routing."""
from __future__ import annotations

import json
import os
import re
import sqlite3
import urllib.request
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone, timedelta
from pathlib import Path
from sktrader.core import AlertError, SnapshotError, feed_fingerprint, new_prefix

SCANNER_CHANNELS = {
    "QUANTUM": "xauusd-quantum",
    "TREND WAVE": "xauusd-trend-wave",
    "AUROUX": "xauusd-auroux",
    "NEXUS": "xauusd-nexus",
    "SNIPER PRO": "xauusd-sniper-pro",
    "WOLF X": "xauusd-wolf-x",
    "HARMONICS": "xauusd-harmonics",
    "PRIME": "xauusd-prime",
}
# Reuse the established #*-signals channels shown in the user's server.
# Do not create another xauusd-* set or touch #gws-signals.
EXISTING_SIGNAL_CHANNELS = {
    "QUANTUM": "quantum-signals",
    "TREND WAVE": "trend-wave-signals",
    "AUROUX": "auroux-signals",
    "NEXUS": "nexus-signals",
    "SNIPER PRO": "sniper-pro-signals",
    "WOLF X": "wolfx-signals",
    "HARMONICS": "harmonics-signals",
    "PRIME": "prime-signals",
}
SCANNER_ALIASES = {
    **{name: name for name in SCANNER_CHANNELS},
    "SNIPERPRO": "SNIPER PRO", "WOLFX": "WOLF X", "TRENDWAVE": "TREND WAVE",
}
AGE = r"(?:(?:\d+|a|an)\s+(?:second|minute|hour|day|week)s?\s+ago|just\s+now)"
HEADER = re.compile(
    rf"^(?P<scanner>[A-Z][A-Z0-9 \-]+?)\s+(?P<age>{AGE})\s+"
    r"(?P<symbol>[A-Z][A-Z0-9.]*)\s+"
    r"(?P<timeframe>1M|5M|15M|30M|1H|4H)\s*-\s*"
    r"(?P<side>BUY|SELL)\s+(?P<body>.+)$", re.I,
)
LABELS = re.compile(
    r"\b(?:Point\s+of\s+Interest|Invalidation|Exit\s*\d+|Level|Zone|"
    r"Entry|Stop\s+Loss|Take\s+Profit\s*\d*)\b", re.I,
)
SNOWFLAKE = re.compile(r"^\d{15,22}$")


@dataclass(frozen=True)
class ScannerSignal:
    scanner: str
    symbol: str
    timeframe: str
    side: str
    relative_age: str
    fields: list[dict[str, str]]
    observed_at: str | None = None


def is_xauusd_card(text: str) -> bool:
    return bool(re.search(r"\bXAUUSD\b(?![A-Za-z0-9.])", text, re.I))


def parse_scanner_card(text: str) -> ScannerSignal:
    """Preserve exactly the fields this scanner displayed, never invent exits."""
    compact = re.sub(r"\s+", " ", text.strip())
    match = HEADER.fullmatch(compact)
    if not match:
        raise AlertError("Invalid scanner signal header")
    if match["symbol"].upper() != "XAUUSD":
        raise AlertError("Non-XAUUSD scanner signal")
    source = re.sub(r"\s+SCANNER$", "", match["scanner"].strip().upper())
    scanner = SCANNER_ALIASES.get(source)
    if scanner not in SCANNER_CHANNELS:
        raise AlertError("Unconfigured XAUUSD scanner")
    body = match["body"]
    tokens = list(LABELS.finditer(body))
    if not tokens or tokens[0].start() != 0:
        raise AlertError("Scanner signal has no labelled Point of Interest")
    fields = []
    for i, token in enumerate(tokens):
        value = body[token.end():tokens[i + 1].start() if i + 1 < len(tokens) else len(body)].strip(" :\t")
        if not value:
            raise AlertError("Missing scanner field value")
        label = re.sub(r"\s+", " ", token.group(0)).strip()
        if re.fullmatch(r"Exit\s*\d+", label, re.I):
            label = "Exit" + re.search(r"\d+", label).group(0)
        fields.append({"name": label, "value": value})
    if not any(x["name"].lower() == "point of interest" for x in fields):
        raise AlertError("Point of Interest missing")
    return ScannerSignal(
        scanner, "XAUUSD", match["timeframe"].upper(),
        match["side"].upper(), match["age"], fields,
    )


def _approximate_source_time(age: str, observed: datetime) -> int | None:
    """JIFU provides relative age, not an exact issuance timestamp."""
    if age.lower() == "just now":
        return int(observed.timestamp())
    match = re.fullmatch(r"(\d+|a|an) (second|minute|hour|day|week)s? ago", age.lower())
    if not match:
        return None
    quantity = 1 if match.group(1) in {"a", "an"} else int(match.group(1))
    seconds = {"second": 1, "minute": 60, "hour": 3600, "day": 86400, "week": 604800}[match.group(2)]
    return int((observed - timedelta(seconds=quantity * seconds)).timestamp())


def discord_card(signal: ScannerSignal, *, observed_at: str | None = None) -> dict:
    """Show original approximate source age and durable detection time separately."""
    timing = signal.relative_age
    if observed_at or signal.observed_at:
        observed = datetime.fromisoformat((observed_at or signal.observed_at).replace("Z", "+00:00"))
        if observed.tzinfo is None:
            raise ValueError("Signal observation timestamp must include timezone")
        observed = observed.astimezone(timezone.utc)
        issued = _approximate_source_time(signal.relative_age, observed)
        if issued is not None:
            timing += f"\n**Approx. JIFU time:** <t:{issued}:F> (estimated from relative age)"
        timing += f"\n**Detected by Sktrader:** <t:{int(observed.timestamp())}:F>"
    return {
        "embeds": [{
            "title": signal.scanner,
            "description": f"{timing}\n\n**{signal.symbol}**\n**{signal.timeframe} - {signal.side}**",
            "color": 0x22C55E if signal.side == "BUY" else 0xEF4444,
            "fields": [{**field, "inline": False} for field in signal.fields],
        }],
        "allowed_mentions": {"parse": []},
    }


class ScannerStore:
    """Separate XAUUSD mixed-feed state from prior Auroux-only SQLite history."""
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(path))
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS scanner_feed_snapshot "
            "(id INTEGER PRIMARY KEY CHECK(id=1), fingerprints TEXT NOT NULL)"
        )
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS scanner_events "
            "(id INTEGER PRIMARY KEY AUTOINCREMENT, observed_at TEXT NOT NULL, "
            "payload TEXT NOT NULL, delivered_at TEXT)"
        )
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS scanner_coverage "
            "(scanner TEXT PRIMARY KEY, last_seen_at TEXT NOT NULL, last_signal_at TEXT)"
        )
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS scanner_history_replay "
            "(id INTEGER PRIMARY KEY CHECK(id=1), queued_at TEXT NOT NULL, count INTEGER NOT NULL)"
        )
        self.conn.commit()

    def _snapshot(self) -> list[str] | None:
        row = self.conn.execute(
            "SELECT fingerprints FROM scanner_feed_snapshot WHERE id=1"
        ).fetchone()
        return json.loads(row[0]) if row else None

    def observe(self, cards: list[str]) -> tuple[int, bool]:
        if not cards:
            raise SnapshotError("Empty mixed scanner feed")
        current = [feed_fingerprint(card) for card in cards]
        previous = self._snapshot()
        new_indices = new_prefix(previous, current) if previous is not None else []
        eligible = []
        now = datetime.now(timezone.utc).isoformat()
        for i in reversed(new_indices):
            if is_xauusd_card(cards[i]):
                try:
                    eligible.append(replace(parse_scanner_card(cards[i]), observed_at=now))
                except AlertError as exc:
                    raise SnapshotError("Unparseable new XAUUSD scanner signal") from exc
        observed_scanners = set()
        for card in cards:
            heading = re.sub(r"\\s+", " ", card.strip()).upper()
            for alias, canonical in SCANNER_ALIASES.items():
                if heading.startswith(alias + " ") and canonical in SCANNER_CHANNELS:
                    observed_scanners.add(canonical)
                    break
        with self.conn:
            for scanner in observed_scanners:
                self.conn.execute(
                    "INSERT INTO scanner_coverage(scanner,last_seen_at) VALUES(?,?) "
                    "ON CONFLICT(scanner) DO UPDATE SET last_seen_at=excluded.last_seen_at",
                    (scanner, now),
                )
            for signal in eligible:
                self.conn.execute(
                    "INSERT INTO scanner_coverage(scanner,last_seen_at,last_signal_at) VALUES(?,?,?) "
                    "ON CONFLICT(scanner) DO UPDATE SET last_seen_at=excluded.last_seen_at, "
                    "last_signal_at=excluded.last_signal_at",
                    (signal.scanner, now, now),
                )
                self.conn.execute(
                    "INSERT INTO scanner_events(observed_at,payload) VALUES(?,?)",
                    (now, json.dumps(asdict(signal), sort_keys=True)),
                )
            self.conn.execute(
                "INSERT INTO scanner_feed_snapshot(id,fingerprints) VALUES(1,?) "
                "ON CONFLICT(id) DO UPDATE SET fingerprints=excluded.fingerprints",
                (json.dumps(current),),
            )
        return len(eligible), previous is None

    def replay_once(self, cards: list[str], *, newest: list[str]) -> tuple[int, bool]:
        if not cards or not newest:
            raise SnapshotError("Empty history or current feed")
        if self.conn.execute("SELECT 1 FROM scanner_history_replay WHERE id=1").fetchone():
            return 0, True
        eligible = []
        now = datetime.now(timezone.utc).isoformat()
        for card in reversed(cards):
            if is_xauusd_card(card):
                try:
                    eligible.append(replace(parse_scanner_card(card), observed_at=now))
                except AlertError as exc:
                    raise SnapshotError("Unparseable historical XAUUSD scanner card") from exc
        current = [feed_fingerprint(card) for card in newest]
        with self.conn:
            for signal in eligible:
                self.conn.execute(
                    "INSERT INTO scanner_events(observed_at,payload) VALUES(?,?)",
                    (now, json.dumps(asdict(signal), sort_keys=True)),
                )
            if eligible:
                self.conn.execute(
                    "INSERT INTO scanner_history_replay(id,queued_at,count) VALUES(1,?,?)",
                    (now, len(eligible)),
                )
            self.conn.execute(
                "INSERT INTO scanner_feed_snapshot(id,fingerprints) VALUES(1,?) "
                "ON CONFLICT(id) DO UPDATE SET fingerprints=excluded.fingerprints",
                (json.dumps(current),),
            )
        return len(eligible), False

    def coverage(self) -> dict[str, dict[str, str | None]]:
        rows = self.conn.execute(
            "SELECT scanner,last_seen_at,last_signal_at FROM scanner_coverage"
        ).fetchall()
        return {name: {"last_seen_at": seen, "last_signal_at": issued}
                for name, seen, issued in rows}

    def pending(self) -> list[tuple[int, ScannerSignal]]:
        rows = self.conn.execute(
            "SELECT id,payload FROM scanner_events WHERE delivered_at IS NULL ORDER BY id"
        ).fetchall()
        return [(id_, ScannerSignal(**json.loads(raw))) for id_, raw in rows]

    def delivered(self, event_id: int) -> None:
        with self.conn:
            self.conn.execute(
                "UPDATE scanner_events SET delivered_at=? "
                "WHERE id=? AND delivered_at IS NULL",
                (datetime.now(timezone.utc).isoformat(), event_id),
            )


def load_channels(path: Path, auroux_channel: int) -> dict[str, int]:
    if not path.exists():
        raise ValueError("Scanner channel map missing. Run setup-channels first.")
    mapping = json.loads(path.read_text(encoding="utf-8"))
    if set(mapping) != set(SCANNER_CHANNELS):
        raise ValueError("Channel map must have all eight scanners")
    if (any(not isinstance(v, str) or not SNOWFLAKE.fullmatch(v) for v in mapping.values())
            or len(set(mapping.values())) != len(mapping)
            or mapping["AUROUX"] != str(auroux_channel)):
        raise ValueError("Invalid or overlapping scanner channel IDs")
    return {name: int(cid) for name, cid in mapping.items()}


def _discord_json(token: str, method: str, route: str, payload: dict | None = None) -> dict | list:
    request = urllib.request.Request(
        f"https://discord.com/api/v10{route}",
        data=json.dumps(payload).encode("utf-8") if payload is not None else None,
        method=method,
        headers={"Authorization": f"Bot {token}", "Content-Type": "application/json",
                 "User-Agent": "sktrader-collector/0.1"},
    )
    with urllib.request.urlopen(request, timeout=12) as response:
        return json.loads(response.read().decode("utf-8"))


def setup_channels(token: str, auroux_channel: int, mapping_path: Path) -> dict[str, int]:
    """Bind existing #*-signals channels. Never create or delete channels.

    The existing AUROUX channel ID anchors the server and category. All eight
    destination IDs are retrieved through authenticated Discord GET requests.
    If anything is missing or ambiguous, leave the previous map untouched.
    """
    source = _discord_json(token, "GET", f"/channels/{auroux_channel}")
    guild_id = str(source.get("guild_id", ""))
    category_id = source.get("parent_id")
    if not SNOWFLAKE.fullmatch(guild_id):
        raise ValueError("Auroux destination is not a Discord server channel")
    if source.get("name") != EXISTING_SIGNAL_CHANNELS["AUROUX"] or source.get("type") != 0:
        raise ValueError("Existing AUROUX channel must be #auroux-signals")
    all_channels = _discord_json(token, "GET", f"/guilds/{guild_id}/channels")
    if not isinstance(all_channels, list):
        raise ValueError("Discord channel listing is invalid")

    mapping = {}
    for scanner, expected_name in EXISTING_SIGNAL_CHANNELS.items():
        matches = [
            ch for ch in all_channels
            if ch.get("name") == expected_name
            and ch.get("parent_id") == category_id
            and ch.get("type") == 0
        ]
        if len(matches) != 1:
            raise ValueError(
                f"Expected exactly one existing #{expected_name} in the same category; "
                f"found {len(matches)}. No mappings changed."
            )
        channel_id = str(matches[0].get("id", ""))
        if not SNOWFLAKE.fullmatch(channel_id):
            raise ValueError(f"Invalid Discord channel ID for #{expected_name}")
        mapping[scanner] = channel_id
    if mapping["AUROUX"] != str(auroux_channel):
        raise ValueError("Mapped AUROUX ID does not match configured existing channel")
    if len(set(mapping.values())) != len(mapping):
        raise ValueError("Scanner channels must have distinct IDs")

    # Save privately and atomically. Keep one copy of the prior xauusd-*
    # mapping so it can be inspected after migration, if necessary.
    mapping_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    old_content = mapping_path.read_bytes() if mapping_path.exists() else None
    backup = mapping_path.with_suffix(".legacy.json")
    if old_content is not None and not backup.exists():
        backup_fd = os.open(str(backup), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(backup_fd, "wb") as handle:
            handle.write(old_content)
    staging = mapping_path.with_suffix(".new")
    fd = os.open(str(staging), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(mapping, handle, sort_keys=True, indent=2)
        os.replace(staging, mapping_path)
    finally:
        if staging.exists():
            staging.unlink()
    return {scanner: int(channel_id) for scanner, channel_id in mapping.items()}
