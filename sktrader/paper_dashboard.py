"""Discord portfolio snapshots from the durable simulated-trade ledger.

No broker order access. A snapshot is an edited Discord message, not an
unbounded stream of new messages.
"""
from __future__ import annotations
from datetime import datetime, timezone
from decimal import Decimal
import json
import sqlite3
import urllib.request
import urllib.error

CHANNEL = "1565846495578558526"


def dashboard(ledger: sqlite3.Connection):
    accounts = []
    for scanner, balance, realized in ledger.execute(
        "SELECT scanner,balance,realized FROM portfolios ORDER BY scanner"):
        positions = ledger.execute(
            "SELECT event_id,side,entry_price,sl,tp,lots FROM paper_signals "
            "WHERE scanner=? AND state='OPEN' ORDER BY event_id", (scanner,)).fetchall()
        closed = ledger.execute(
            "SELECT state,net_pnl FROM paper_signals WHERE scanner=? "
            "AND state IN ('TP1','SL')", (scanner,)).fetchall()
        wins = sum(1 for state,_ in closed if state == "TP1")
        losses = len(closed)-wins
        orders = ledger.execute(
            "SELECT COUNT(*) FROM paper_signals WHERE scanner=? AND state='PENDING'",
            (scanner,)).fetchone()[0]
        accounts.append({"scanner":scanner,"balance":str(balance),
                         "realized":str(realized),"wins":wins,"losses":losses,
                         "pending":orders,"open":positions})
    fields = []
    for a in accounts:
        win_rate = f"{100*a['wins']/(a['wins']+a['losses']):.1f}%" if a["wins"]+a["losses"] else "N/A"
        line = (f"Balance: **${Decimal(a['balance']):,.2f}** | "
                f"Realized: ${Decimal(a['realized']):,.2f}\n"
                f"Closed: {a['wins']+a['losses']} | W: {a['wins']} | L: {a['losses']} "
                f"| WR: {win_rate}\n"
                f"Pending: {a['pending']} | Open: {len(a['open'])}")
        for eid,side,entry,sl,tp,lots in a["open"][:3]:
            line += f"\n#{eid} {side} {lots} @ {entry} | SL {sl} | TP1 {tp}"
        fields.append({"name":a["scanner"],"value":line,"inline":False})
    return {"embeds":[{"title":"DK Capital | Paper Portfolios (SIMULATED)",
                        "description":"Independent virtual USD books, each started at $1,000. "
                        "Paper fills are not broker executions. "
                        "Equity and unrealized P&L are not shown until quote-mark valuation is commissioned.",
                        "color":3447003,
                        "fields":fields,
                        "footer":{"text":"Updated "+datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")}}],
            "allowed_mentions":{"parse":[]}}


class DashboardPublisher:
    def __init__(self, ledger, channel=CHANNEL):
        self.db=ledger
        self.channel=channel
        self.db.execute("CREATE TABLE IF NOT EXISTS dashboard_meta("
                        "key TEXT PRIMARY KEY,value TEXT NOT NULL)")
        self.db.commit()

    def publish(self, token):
        payload=dashboard(self.db)
        row=self.db.execute("SELECT value FROM dashboard_meta WHERE key='message_id'").fetchone()
        mid=row[0] if row else None
        if mid:
            req=urllib.request.Request(
                f"https://discord.com/api/v10/channels/{self.channel}/messages/{mid}",
                data=json.dumps(payload).encode(),method="PATCH",
                headers={"Authorization":"Bot "+token,"Content-Type":"application/json"})
            try:
                with urllib.request.urlopen(req,timeout=12):
                    return "edited"
            except urllib.error.HTTPError as exc:
                if exc.code not in (404,):
                    raise
        req=urllib.request.Request(
            f"https://discord.com/api/v10/channels/{self.channel}/messages",
            data=json.dumps(payload).encode(),method="POST",
            headers={"Authorization":"Bot "+token,"Content-Type":"application/json"})
        with urllib.request.urlopen(req,timeout=12) as resp:
            message=json.load(resp)
        if not str(message.get("id","")).isdigit():
            raise RuntimeError("Discord did not return message ID")
        with self.db:
            self.db.execute("INSERT INTO dashboard_meta(key,value) VALUES('message_id',?) "
                            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                            (message["id"],))
        return "created"
