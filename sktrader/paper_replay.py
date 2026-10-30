"""Read-only paper replay with durable signal/order/fill audit and Discord outbox.

This never submits MT5 orders. Signal acceptance is bounded by detected time
and scanner-relative source age; market tick processing is strictly ordered.
"""
from __future__ import annotations
import argparse
from datetime import datetime, timezone, timedelta
import json
import os
from pathlib import Path
import sqlite3
import time
import urllib.request
import urllib.error

from sktrader.market_feed import MarketFeed
from sktrader.paper_portfolios import PaperPortfolios, SCANNERS, timestamp
from sktrader.paper_dashboard import DashboardPublisher

DEFAULT_CHANNEL = "1565846495578558526"


def source_age_seconds(text):
    words = str(text).strip().lower().split()
    if text == "just now":
        return 0
    if len(words) != 3 or words[2] != "ago":
        return None
    try:
        n = 1 if words[0] in ("a", "an") else int(words[0])
    except ValueError:
        return None
    unit = words[1].rstrip("s")
    return n * {"second":1, "minute":60, "hour":3600, "day":86400,
                "week":604800}.get(unit, 10**9)


class PaperReplay:
    def __init__(self, signal_db: Path, market_db: Path, paper_db: Path, *, bootstrap=True):
        self.signals = sqlite3.connect(f"file:{signal_db}?mode=ro", uri=True)
        self.market = sqlite3.connect(f"file:{market_db}?mode=ro", uri=True)
        self.engine = PaperPortfolios(paper_db)
        self.engine.db.executescript("""
            CREATE TABLE IF NOT EXISTS replay_state(
              key TEXT PRIMARY KEY, value INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS paper_audit(
              id INTEGER PRIMARY KEY AUTOINCREMENT, event_id INTEGER NOT NULL,
              scanner TEXT NOT NULL, kind TEXT NOT NULL, recorded_at TEXT NOT NULL,
              detail TEXT NOT NULL,
              UNIQUE(event_id,kind));
            CREATE TABLE IF NOT EXISTS discord_outbox(
              audit_id INTEGER PRIMARY KEY, sent_at TEXT, message_id TEXT,
              FOREIGN KEY(audit_id) REFERENCES paper_audit(id));
        """)
        if "message_id" not in [x[1] for x in self.engine.db.execute("PRAGMA table_info(discord_outbox)")]:
            self.engine.db.execute("ALTER TABLE discord_outbox ADD COLUMN message_id TEXT")
        with self.engine.db:
            if self._state("signal_cursor") is None:
                cursor = self.signals.execute("SELECT COALESCE(MAX(id),0) FROM scanner_events").fetchone()[0] if bootstrap else 0
                self._set_state("signal_cursor", cursor)
            if self._state("source_delivery_cursor") is None:
                baseline=self.signals.execute("SELECT COALESCE(MAX(id),0) FROM scanner_events").fetchone()[0] if bootstrap else 0
                self._set_state("source_delivery_cursor", baseline)
            if self._state("tick_cursor") is None:
                cursor = self.market.execute("SELECT COALESCE(MAX(rowid),0) FROM feed_ticks").fetchone()[0] if bootstrap else 0
                self._set_state("tick_cursor", cursor)

    def _state(self, key):
        row = self.engine.db.execute("SELECT value FROM replay_state WHERE key=?", (key,)).fetchone()
        return row[0] if row else None

    def _set_state(self, key, value):
        self.engine.db.execute("INSERT INTO replay_state(key,value) VALUES (?,?) "
                               "ON CONFLICT(key) DO UPDATE SET value=excluded.value",(key,value))

    def _audit(self, event_id, scanner, kind, detail, stamp):
        self.engine.db.execute("INSERT OR IGNORE INTO paper_audit "
                               "(event_id,scanner,kind,recorded_at,detail) VALUES (?,?,?,?,?)",
                               (event_id,scanner,kind,stamp,json.dumps(detail,sort_keys=True)))
        row=self.engine.db.execute("SELECT id FROM paper_audit WHERE event_id=? AND kind=?",
                                   (event_id,kind)).fetchone()
        self.engine.db.execute("INSERT OR IGNORE INTO discord_outbox(audit_id) VALUES (?)",(row[0],))

    def tick(self, now=None):
        now=datetime.now(timezone.utc) if now is None else timestamp(now)
        # Collect newly detected signals. Historical records are never replayed
        # on the initial service activation.
        for eid,observed,payload in self.signals.execute(
            "SELECT id,observed_at,payload FROM scanner_events WHERE id>? ORDER BY id",
            (self._state("signal_cursor"),)):
            signal=json.loads(payload)
            scanner=signal.get("scanner")
            with self.engine.db:
                if scanner in SCANNERS:
                    age=source_age_seconds(signal.get("relative_age",""))
                    observed_dt=timestamp(observed)
                    if age is None or age>=180:
                        state="SKIPPED"
                        self.engine.accept(eid,{**signal,"observed_at":observed},expires_at=observed)
                    else:
                        expiry=observed_dt + timedelta(seconds=180-age)
                        state=self.engine.accept(eid,{**signal,"observed_at":observed},
                                                 expires_at=expiry.isoformat())
                    self._audit(eid,scanner,"SIGNAL_RECEIVED",
                                {"side":signal.get("side"),"fields":signal.get("fields"),
                                 "observed_at":observed,"relative_age":signal.get("relative_age")},
                                observed)
                    if state!="PENDING":
                        self._audit(eid,scanner,"SIGNAL_"+state,
                                    {"state":state,"reason":"invalid, missing levels or aged signal"},
                                    now.isoformat())
                    else:
                        self._audit(eid,scanner,"ORDER_PENDING",
                                    {"status":"paper only, awaiting executable bid/ask"},
                                    now.isoformat())
                self._set_state("signal_cursor",eid)
        # A successful source-channel Discord POST is independently recorded
        # by ScannerStore.delivered(). Do not mistake detection for delivery.
        for eid,delivered,payload in self.signals.execute(
            "SELECT id,delivered_at,payload FROM scanner_events WHERE id>? "
            "ORDER BY id LIMIT 200", (self._state("source_delivery_cursor"),)):
            if not delivered:
                break
            scanner=json.loads(payload).get("scanner")
            with self.engine.db:
                if scanner in SCANNERS:
                    self._audit(eid,scanner,"SOURCE_DISCORD_DELIVERED",
                                {"delivered_at":delivered,
                                 "evidence":"Sktrader source watcher acknowledged successful Discord HTTP response"},
                                delivered)
                self._set_state("source_delivery_cursor",eid)
        # Source feed is trusted only for this demo profile. Paper calculations use
        # USD XAUUSD quote/contract conventions, NOT the GBP account tick_value.
        ident=self.market.execute("SELECT login,mode,currency,symbol,contract_size,generated_at "
                                  "FROM feed_identity WHERE id=1").fetchone()
        if not ident or ident[1]!="DEMO" or ident[3]!="XAUUSD" or str(ident[4])!="100.0000":
            raise RuntimeError("broker identity/contract unverified")
        if (now-datetime.fromtimestamp(ident[5],tz=timezone.utc)).total_seconds() > 120:
            raise RuntimeError("broker identity stale")
        # Only post-activation quote ticks. Do not create retroactive wins.
        query=("SELECT rowid,time_msc,bid,ask FROM feed_ticks WHERE rowid>? "
               "ORDER BY rowid LIMIT 2000")
        rows=self.market.execute(query,(self._state("tick_cursor"),)).fetchall()
        for rid,stamp_ms,bid,ask in rows:
            stamp=datetime.fromtimestamp(stamp_ms/1000,tz=timezone.utc)
            # Refuse old quotes and excessive spread; do not advance tick cursor
            # for out-of-order arrivals as these need explicit operator review.
            last=self.engine.db.execute("SELECT stamp FROM quotes ORDER BY seq DESC LIMIT 1").fetchone()
            if last and stamp <= timestamp(last[0]):
                raise RuntimeError("out-of-order tick feed; stop paper replay")
            if last and (stamp-timestamp(last[0])).total_seconds() > 5:
                if self.engine.db.execute("SELECT 1 FROM paper_signals WHERE state='OPEN' LIMIT 1").fetchone():
                    raise RuntimeError("market data gap with open paper position: manual reconciliation required")
            with self.engine.db:
                if (now-stamp).total_seconds() > 30 or (stamp-now).total_seconds() > 5 or (float(ask)-float(bid)) > 1.50:
                    self._set_state("tick_cursor",rid)
                    continue
                before={r[0]:r[1] for r in self.engine.db.execute(
                    "SELECT event_id,state FROM paper_signals WHERE state IN ('OPEN','PENDING')")}
                self.engine.on_quote(observed_at=stamp.isoformat(),bid=bid,ask=ask)
                for eid,previous in before.items():
                    row=self.engine.db.execute(
                        "SELECT scanner,state,entry_price,exit_price,lots,net_pnl FROM paper_signals "
                        "WHERE event_id=?", (eid,)).fetchone()
                    if row and row[1]!=previous:
                        scanner,state,entry,exit_price,lots,pnl=row
                        kind=({"OPEN":"PAPER_FILLED","TP1":"PAPER_CLOSED_TP1",
                               "SL":"PAPER_CLOSED_SL","EXPIRED":"ORDER_EXPIRED",
                               "SKIPPED":"ORDER_SKIPPED"}.get(state,"STATE_"+state))
                        self._audit(eid,scanner,kind,
                                    {"prior":previous,"state":state,"entry":entry,
                                     "exit":exit_price,"lots":lots,"net_pnl_usd":pnl,
                                     "market_time":stamp.isoformat()},now.isoformat())
                self._set_state("tick_cursor",rid)
        # Expire pending simulation intents using wall-clock time even when
        # XAUUSD is closed and no new broker quotes arrive.
        with self.engine.db:
            for eid,scanner,expiry in self.engine.db.execute(
                "SELECT event_id,scanner,expiry FROM paper_signals "
                "WHERE state='PENDING' AND expiry IS NOT NULL").fetchall():
                if now >= timestamp(expiry):
                    self.engine.db.execute(
                        "UPDATE paper_signals SET state='EXPIRED' WHERE event_id=?", (eid,))
                    self._audit(eid,scanner,"ORDER_EXPIRED",
                                {"state":"EXPIRED","reason":"signal validity window elapsed"},
                                now.isoformat())
        return len(rows)

    def reconcile_audit(self):
        """Recover paper fills/closes after a crash between ledger and outbox writes."""
        with self.engine.db:
            for eid,scanner,state,entry_time,entry,lots,exit_time,exit_price,net in self.engine.db.execute(
                "SELECT event_id,scanner,state,entry_time,entry_price,lots,"
                "exit_time,exit_price,net_pnl FROM paper_signals ORDER BY event_id"
            ).fetchall():
                if entry_time:
                    self._audit(eid,scanner,"PAPER_FILLED",
                                {"entry":entry,"lots":lots,"market_time":entry_time,
                                 "state":"OPEN","simulation":True},
                                entry_time)
                if state in ("TP1","SL") and exit_time:
                    self._audit(eid,scanner,"PAPER_CLOSED_"+state,
                                {"entry":entry,"exit":exit_price,"lots":lots,
                                 "net_pnl_usd":net,"market_time":exit_time,
                                 "simulation":True},
                                exit_time)
                if state == "EXPIRED":
                    self._audit(eid,scanner,"ORDER_EXPIRED",
                                {"state":"EXPIRED"},datetime.now(timezone.utc).isoformat())

    def pending_notifications(self):
        return list(self.engine.db.execute(
            "SELECT a.id,a.event_id,a.scanner,a.kind,a.recorded_at,a.detail "
            "FROM paper_audit a JOIN discord_outbox o ON o.audit_id=a.id "
            "WHERE o.sent_at IS NULL ORDER BY a.id LIMIT 100"))

    def mark_sent(self, audit_id, message_id):
        if not str(message_id).isdigit():
            raise ValueError("Discord message id missing")
        with self.engine.db:
            self.engine.db.execute("UPDATE discord_outbox SET sent_at=?,message_id=? WHERE audit_id=?",
                                   (datetime.now(timezone.utc).isoformat(),str(message_id),audit_id))


def notify_discord(channel, token, event):
    aid,eid,scanner,kind,stamp,detail=event
    fields=json.loads(detail)
    text=f"**{scanner} | {kind.replace('_',' ')} | SIMULATED**\nSignal #{eid}\n"
    text+= "\n".join(f"{k}: {str(v)[:350]}" for k,v in fields.items())
    req=urllib.request.Request(
        f"https://discord.com/api/v10/channels/{channel}/messages",
        data=json.dumps({"content":text[:1900],
                         "allowed_mentions":{"parse":[]},
                         "nonce":f"sktrader-paper-{aid}","enforce_nonce":True}).encode(),
        headers={"Authorization":"Bot "+token,"Content-Type":"application/json",
                 "User-Agent":"sktrader-paper-replay/1.0"},method="POST")
    with urllib.request.urlopen(req,timeout=12) as response:
        if response.status != 200:
            raise RuntimeError(f"Discord response {response.status}")
        message=json.load(response)
        if not str(message.get("id","")).isdigit():
            raise RuntimeError("Discord did not acknowledge message id")
        return message["id"]


def main():
    p=argparse.ArgumentParser()
    p.add_argument("--signals",type=Path,required=True)
    p.add_argument("--market",type=Path,required=True)
    p.add_argument("--paper",type=Path,required=True)
    p.add_argument("--common",type=Path,required=True)
    p.add_argument("--expected-login",type=int,required=True)
    p.add_argument("--token-file",type=Path,required=True)
    p.add_argument("--channel",default=DEFAULT_CHANNEL)
    p.add_argument("--interval",type=float,default=1)
    args=p.parse_args()
    if args.interval<0.25 or not args.channel.isascii() or not args.channel.isdigit():
        p.error("bad interval/channel")
    import stat
    if args.token_file.stat().st_mode & (stat.S_IRWXG|stat.S_IRWXO):
        p.error("token file must be owner-only")
    token=args.token_file.read_text().strip()
    feed=MarketFeed(args.market,expected_login=args.expected_login)
    replay=PaperReplay(args.signals,args.market,args.paper)
    dashboard=DashboardPublisher(replay.engine.db, channel=args.channel)
    last_dashboard=0
    while True:
        # Signal receipt/audit delivery must continue even when broker
        # prices are unavailable. No quote-derived fills are attempted then.
        try:
            feed.ingest(args.common)
        except (OSError,ValueError,RuntimeError,sqlite3.Error) as exc:
            print("market ingest unavailable:",str(exc)[:240],flush=True)
        try:
            replay.tick()
        except (OSError,ValueError,RuntimeError,sqlite3.Error) as exc:
            print("paper execution paused:",str(exc)[:240],flush=True)
        try:
            replay.reconcile_audit()
            for event in replay.pending_notifications():
                message_id=notify_discord(args.channel,token,event)
                replay.mark_sent(event[0],message_id)
            if time.monotonic()-last_dashboard > 60:
                dashboard.publish(token)
                last_dashboard=time.monotonic()
        except (OSError,ValueError,RuntimeError,sqlite3.Error,urllib.error.URLError) as exc:
            print("paper Discord reporting unavailable:",str(exc)[:240],flush=True)
        time.sleep(args.interval)

if __name__=="__main__":
    main()
