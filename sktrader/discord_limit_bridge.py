"""Low-latency Discord channel -> MT5 signal file. Never places broker orders.

Only fresh MESSAGE_CREATE events from one pinned Discord channel and an
explicit trusted-author allowlist are eligible. The EA places limit orders.
"""
from __future__ import annotations

import argparse
import asyncio
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import json
import logging
import math
import os
from pathlib import Path
import random
import re
import sqlite3
import time
import urllib.request

import websockets

LOG = logging.getLogger("sktrader.discord_limit")
CHANNEL_ID = "1565527578163552327"
DISCORD_EPOCH_MS = 1420070400000
LIFETIME_SECONDS = 1800
GATEWAY_INTENTS = 1 << 9  # GUILD_MESSAGES; own bot messages need no privileged content intent
GATEWAY = "wss://gateway.discord.gg/?v=10&encoding=json"
PRICE = re.compile(r"[0-9]+(?:\.[0-9]+)?")
SIDE = re.compile(r"\b(?:1M|5M|15M|30M|1H|4H)\s*[-:]\s*(BUY|SELL)\b", re.I)
SYMBOL = re.compile(r"\bXAUUSD\b", re.I)
LABEL = re.compile(r"(?im)^\s*\*{0,2}Point\s+of\s+Interest\*{0,2}\s*:?\s*(?:\n\s*)?([0-9]+(?:\.[0-9]+)?)\s*$")


def message_time_ms(message_id: str) -> int:
    if not re.fullmatch(r"[0-9]{15,22}", message_id):
        raise ValueError("invalid Discord message snowflake")
    return (int(message_id) >> 22) + DISCORD_EPOCH_MS


def extract_signal(message: dict, *, channel_id: str, author_id: str | set[str] | frozenset[str] | tuple[str, ...], now_ms: int | None = None,
                   not_before_ms: int = 0):
    """Return (id, side, POI, created_s, expiry_s), or None if ineligible."""
    if str(message.get("channel_id")) != channel_id:
        return None
    authors = {author_id} if isinstance(author_id, str) else set(author_id)
    if not authors or str((message.get("author") or {}).get("id")) not in authors:
        return None
    msg_id = str(message.get("id", ""))
    try:
        created = message_time_ms(msg_id)
    except ValueError:
        return None
    current = int(time.time() * 1000) if now_ms is None else now_ms
    # A persistent cutover epoch prevents Gateway reconnect/resume replay of
    # previously posted signals, even while their 30-minute TTL remains valid.
    if created < not_before_ms:
        return None
    if created > current + 5000 or current >= created + LIFETIME_SECONDS * 1000:
        return None

    bodies = [str(message.get("content") or "")]
    pois = []
    embeds = message.get("embeds") or []
    if not isinstance(embeds, list):
        return None
    for embed in embeds:
        if not isinstance(embed, dict):
            return None
        bodies.append(str(embed.get("title") or ""))
        bodies.append(str(embed.get("description") or ""))
        for field in embed.get("fields") or []:
            if not isinstance(field, dict):
                return None
            name = re.sub(r"\s+", " ", str(field.get("name") or "")).strip().casefold()
            if name == "point of interest":
                pois.append(str(field.get("value") or "").strip())
    combined = "\n".join(bodies)
    if not SYMBOL.search(combined):
        return None
    matches = SIDE.findall(combined)
    if len(set(x.upper() for x in matches)) != 1:
        return None
    if not pois:
        pois = LABEL.findall(combined)
    if len(pois) != 1 or not PRICE.fullmatch(pois[0]):
        return None
    price = float(pois[0])
    if not math.isfinite(price) or price <= 0:
        return None
    # The connected MetaQuotes-Demo XAUUSD contract quotes in cents. User
    # explicitly requested converting incoming 5-decimal scanner POIs to .xx.
    # Decimal avoids binary-float rounding surprises at half-cent boundaries.
    # The EA still independently validates the actual broker tick grid.
    try:
        rounded = Decimal(pois[0]).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    except InvalidOperation:
        return None
    if rounded <= 0:
        return None
    return (msg_id, matches[0].upper(), format(rounded, ".2f"), created // 1000,
            created // 1000 + LIFETIME_SECONDS)


class SignalStore:
    def __init__(self, db: Path, output: Path):
        db.parent.mkdir(parents=True, exist_ok=True)
        self.output = output
        self.con = sqlite3.connect(db, timeout=5)
        self.con.execute("PRAGMA journal_mode=WAL")
        self.con.execute("""CREATE TABLE IF NOT EXISTS limit_messages(
            id TEXT PRIMARY KEY, side TEXT NOT NULL, poi TEXT NOT NULL,
            issued_s INTEGER NOT NULL, expires_s INTEGER NOT NULL,
            received_ms INTEGER NOT NULL)""")
        self.con.commit()
        self.publish()

    def add(self, signal: tuple, *, now_ms: int | None = None) -> bool:
        msg_id, side, poi, issued, expires = signal
        receipt = int(time.time() * 1000) if now_ms is None else now_ms
        if receipt >= expires * 1000:
            return False
        with self.con:
            cur = self.con.execute(
                "INSERT OR IGNORE INTO limit_messages VALUES(?,?,?,?,?,?)",
                (msg_id, side, poi, issued, expires, receipt),
            )
        if cur.rowcount != 1:
            return False
        self.publish(now_s=receipt // 1000)
        return True

    def publish(self, *, now_s: int | None = None):
        now_s = int(time.time()) if now_s is None else now_s
        rows = self.con.execute(
            "SELECT id,side,poi,issued_s,expires_s FROM limit_messages "
            "WHERE expires_s>? ORDER BY issued_s,id", (now_s,)
        ).fetchall()
        self.output.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.output.with_name(self.output.name + ".new")
        # Atomic replacement: MT5 never sees a partially written row.
        fd = os.open(temporary, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
        try:
            with os.fdopen(fd, "w", encoding="ascii", newline="") as f:
                for row in rows:
                    f.write(";".join(map(str, row)) + "\n")
                f.flush()
                os.fsync(f.fileno())
            os.replace(temporary, self.output)
        finally:
            if temporary.exists():
                temporary.unlink()


def bot_profile(token: str, channel_id: str) -> str:
    def get(route: str):
        req = urllib.request.Request(
            "https://discord.com/api/v10" + route,
            headers={"Authorization": "Bot " + token,
                     "User-Agent": "sktrader-discord-limit/1.0"},
        )
        with urllib.request.urlopen(req, timeout=15) as res:
            return json.load(res)
    bot = get("/users/@me")
    channel = get("/channels/" + channel_id)
    if str(channel.get("id")) != channel_id or not channel.get("guild_id"):
        raise ValueError("target channel not a readable guild text channel")
    return str(bot["id"])


async def gateway_loop(token: str, store: SignalStore, channel_id: str,
                       author_id: str | set[str] | frozenset[str] | tuple[str, ...],
                       *, foreign_author: bool = False, not_before_ms: int = 0):
    session = None
    sequence = None
    resume_url = GATEWAY
    backoff = 1
    while True:
        try:
            async with websockets.connect(resume_url, ping_interval=None, max_size=2_000_000) as ws:
                hello = json.loads(await asyncio.wait_for(ws.recv(), timeout=20))
                if hello.get("op") != 10:
                    raise RuntimeError("Discord Gateway did not send HELLO")
                interval = hello["d"]["heartbeat_interval"] / 1000
                acknowledged = True

                async def send(op, data):
                    await ws.send(json.dumps({"op": op, "d": data}, separators=(",", ":")))

                async def heartbeat():
                    nonlocal acknowledged
                    await asyncio.sleep(interval * random.random())
                    while True:
                        if not acknowledged:
                            LOG.error("Discord heartbeat ACK missing; reconnecting")
                            await ws.close()
                            return
                        acknowledged = False
                        await send(1, sequence)
                        await asyncio.sleep(interval)

                if session is not None and sequence is not None:
                    await send(6, {"token": token, "session_id": session, "seq": sequence})
                else:
                    await send(2, {
                        "token": token, "intents": GATEWAY_INTENTS | ((1 << 15) if foreign_author else 0),
                        "properties": {"os": "linux", "browser": "sktrader",
                                       "device": "sktrader"},
                    })
                hb = asyncio.create_task(heartbeat())
                try:
                    async for raw in ws:
                        event = json.loads(raw)
                        if event.get("s") is not None:
                            sequence = event["s"]
                        op = event.get("op")
                        if op == 11:
                            acknowledged = True
                        elif op == 1:
                            await send(1, sequence)
                        elif op == 7:
                            LOG.warning("Discord requested reconnect")
                            break
                        elif op == 9:
                            if not event.get("d"):
                                session, sequence, resume_url = None, None, GATEWAY
                            break
                        elif op == 0 and event.get("t") == "READY":
                            session = event["d"]["session_id"]
                            resume_url = event["d"].get("resume_gateway_url", GATEWAY)
                            if "?" not in resume_url:
                                resume_url += "?v=10&encoding=json"
                            LOG.info("Discord Gateway connected; channel %s, trusted authors %s, cutoff_ms=%s",
                                     channel_id, ",".join(sorted({author_id} if isinstance(author_id,str) else author_id)),
                                     not_before_ms)
                            backoff = 1
                        elif op == 0 and event.get("t") == "RESUMED":
                            LOG.info("Discord Gateway resumed")
                            backoff = 1
                        elif op == 0 and event.get("t") == "MESSAGE_CREATE":
                            signal = extract_signal(event["d"], channel_id=channel_id,
                                                    author_id=author_id,
                                                    not_before_ms=not_before_ms)
                            if signal and store.add(signal):
                                LOG.info("Limit signal queued: id=%s side=%s POI=%s", *signal[:3])
                finally:
                    hb.cancel()
                    try:
                        await hb
                    except asyncio.CancelledError:
                        pass
        except websockets.exceptions.ConnectionClosed as exc:
            if exc.code in (4004, 4013, 4014):
                raise RuntimeError(f"Discord Gateway authentication/intents rejected ({exc.code})") from exc
            LOG.warning("Discord connection closed: %s", exc)
        except (OSError, TimeoutError, RuntimeError, ValueError, KeyError) as exc:
            LOG.warning("Discord Gateway unavailable: %s", type(exc).__name__)
        await asyncio.sleep(backoff)
        backoff = min(backoff * 2, 30)


def main():
    p = argparse.ArgumentParser(description="One-channel realtime Discord-to-MT5 limit bridge")
    p.add_argument("--channel-id", default=CHANNEL_ID)
    p.add_argument("--token-file", required=True, type=Path)
    p.add_argument("--database", required=True, type=Path)
    p.add_argument("--output", required=True, type=Path)
    p.add_argument("--author-id", action="append",
                   help="Trusted Discord message author; repeat for an explicit allowlist. Defaults to this bot.")
    p.add_argument("--not-before-ms", type=int, default=0,
                   help="Ignore messages created before this UTC millisecond epoch, including reconnect replay")
    args = p.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if not re.fullmatch(r"[0-9]{15,22}", args.channel_id):
        p.error("invalid Discord channel id")
    token = args.token_file.read_text(encoding="utf-8").strip()
    if not token:
        p.error("empty bot token file")
    bot_id = bot_profile(token, args.channel_id)
    author_ids = set(args.author_id) if args.author_id else {bot_id}
    if not author_ids or any(not re.fullmatch(r"[0-9]{15,22}", user_id) for user_id in author_ids):
        p.error("invalid trusted author id")
    if args.not_before_ms < 0 or args.not_before_ms > int(time.time() * 1000) + 5000:
        p.error("invalid not-before cutover timestamp")
    store = SignalStore(args.database, args.output)
    asyncio.run(gateway_loop(token, store, args.channel_id, author_ids,
                             foreign_author=any(user_id != bot_id for user_id in author_ids),
                             not_before_ms=args.not_before_ms))


if __name__ == "__main__":
    main()
