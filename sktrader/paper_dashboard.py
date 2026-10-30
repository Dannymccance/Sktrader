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
    quote = ledger.execute("SELECT stamp,bid,ask FROM quotes ORDER BY seq DESC LIMIT 1").fetchone()
    bid, ask = (Decimal(quote[1]),Decimal(quote[2])) if quote else (None,None)
    now = datetime.now(timezone.utc)
    quote_fresh = bool(quote and abs((now-datetime.fromisoformat(quote[0])).total_seconds()) <= 30)
    accounts = []
    for scanner, balance, realized in ledger.execute(
        "SELECT scanner,balance,realized FROM portfolios ORDER BY scanner"):
        positions = ledger.execute(
            "SELECT event_id,side,entry_price,sl,tp,lots FROM paper_signals "
            "WHERE scanner=? AND state='OPEN' ORDER BY event_id", (scanner,)).fetchall()
        closed = ledger.execute(
            "SELECT state,net_pnl,exit_time FROM paper_signals WHERE scanner=? "
            "AND state IN ('TP1','SL')", (scanner,)).fetchall()
        # Cash-balance drawdown from realized exits only; this is not
        # an intratrade equity drawdown estimate.
        curve=Decimal("1000")
        peak=curve
        max_drawdown=Decimal("0")
        for (profit,) in ledger.execute(
            "SELECT net_pnl FROM paper_signals WHERE scanner=? "
            "AND state IN ('TP1','SL') ORDER BY exit_time,event_id", (scanner,)):
            curve+=Decimal(profit)
            peak=max(peak,curve)
            if peak>0:
                max_drawdown=max(max_drawdown,(peak-curve)*100/peak)
        wins = sum(1 for state,_,_ in closed if state == "TP1")
        losses = len(closed)-wins
        week = now.isocalendar()
        weekly = sum((Decimal(v) for _,v,t in closed if t and
                      datetime.fromisoformat(t).isocalendar()[:2] == week[:2]),
                     Decimal("0"))
        orders = ledger.execute(
            "SELECT COUNT(*) FROM paper_signals WHERE scanner=? AND state='PENDING'",
            (scanner,)).fetchone()[0]
        unrealized = Decimal("0")
        for _,side,entry,_,_,lots in positions:
            if bid is not None and ask is not None:
                mark = bid if side == "BUY" else ask
                unrealized += (mark-Decimal(entry) if side == "BUY" else
                               Decimal(entry)-mark)*Decimal(lots)*Decimal("100")
        accounts.append({"scanner":scanner,"balance":Decimal(balance),
                         "realized":Decimal(realized),"wins":wins,"losses":losses,
                         "weekly":weekly,"drawdown":max_drawdown,"pending":orders,"open":positions,
                         "unrealized":unrealized})
    fields = []
    for a in accounts:
        win_rate = f"{100*a['wins']/(a['wins']+a['losses']):.1f}%" if a["wins"]+a["losses"] else "N/A"
        line = (f"Balance: **${a['balance']:,.2f}** | "
                f"Realized: ${a['realized']:,.2f}\n"
                f"Closed: {a['wins']+a['losses']} | W: {a['wins']} | L: {a['losses']} "
                f"| WR: {win_rate}\n"
                f"Weekly realized: ${a['weekly']:,.2f} | Realized DD: {a['drawdown']:.1f}%\n"
                f"Pending: {a['pending']} | Open: {len(a['open'])}")
        if quote_fresh:
            line += f"\n**Indicative equity: ${a['balance']+a['unrealized']:,.2f}** | Floating: ${a['unrealized']:,.2f}"
        else:
            line += "\nEquity: N/A (quote missing or stale)"
        for eid,side,entry,sl,tp,lots in a["open"][:3]:
            line += f"\n#{eid} {side} {lots} @ {entry} | SL {sl} | TP1 {tp}"
        fields.append({"name":a["scanner"],"value":line[:1024],"inline":False})
    return {"embeds":[{"title":"DK Capital | Paper Portfolios (SIMULATED)",
                        "description":"Five independent USD virtual books, each started at $1,000. "
                        "Paper fills are not broker executions. "
                        "Equity is indicative when the latest quote is fresh; fees/slippage are not fully calibrated.",
                        "color":3447003,
                        "fields":fields,
                        "footer":{"text":"Updated "+now.strftime("%Y-%m-%d %H:%M UTC")}}],
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
                headers={"Authorization":"Bot "+token,"Content-Type":"application/json","User-Agent":"DKCapital-Sktrader-Reporting/1.0"})
            try:
                with urllib.request.urlopen(req,timeout=12):
                    return "edited"
            except urllib.error.HTTPError as exc:
                if exc.code not in (404,):
                    raise
        req=urllib.request.Request(
            f"https://discord.com/api/v10/channels/{self.channel}/messages",
            data=json.dumps(payload).encode(),method="POST",
            headers={"Authorization":"Bot "+token,"Content-Type":"application/json","User-Agent":"DKCapital-Sktrader-Reporting/1.0"})
        with urllib.request.urlopen(req,timeout=12) as resp:
            message=json.load(resp)
        if not str(message.get("id","")).isdigit():
            raise RuntimeError("Discord did not return message ID")
        with self.db:
            self.db.execute("INSERT INTO dashboard_meta(key,value) VALUES('message_id',?) "
                            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                            (message["id"],))
        return "created"
