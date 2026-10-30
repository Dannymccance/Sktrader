"""Verified, read-only AUROUX DEMO account reporting from MT5 common files.

Broker deal tickets are the only source of broker fill/closure confirmations.
Do not merge the GBP demo account with archived Vantage REAL/USD reporting.
"""
from __future__ import annotations
import argparse
import csv
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import json
from pathlib import Path
import sqlite3
import time
import urllib.request
import urllib.error


ACCOUNT_COLS = ["login","mode","currency","server","generated_at","balance",
                "equity","margin","margin_free","floating_profit","positions","orders"]
POSITION_COLS = ["ticket","identifier","symbol","type","magic","volume",
                 "price_open","sl","tp","price_current","profit","swap","time_msc"]
DEAL_COLS = ["ticket","order","position_id","symbol","type","entry",
             "magic","time_msc","volume","price","profit","commission","swap","fee"]


def read_csv(path, columns):
    with path.open(encoding="utf-8-sig",newline="") as fh:
        rows=csv.DictReader(fh,delimiter=";")
        if rows.fieldnames != columns:
            raise ValueError("invalid broker telemetry schema")
        return list(rows)


def amount(raw):
    try:
        v=Decimal(str(raw))
        if not v.is_finite():
            raise ValueError("nonfinite")
        return v
    except (InvalidOperation,ValueError,TypeError) as e:
        raise ValueError("invalid broker numeric field") from e


class BrokerStore:
    def __init__(self, database: Path, *, expected_login: int,
                 expected_server="MetaQuotes-Demo", auroux_magic=2601030):
        if expected_login<=0 or not expected_server:
            raise ValueError("exact demo broker identity required")
        self.expected_login=expected_login
        self.expected_server=expected_server
        self.auroux_magic=auroux_magic
        database.parent.mkdir(parents=True,exist_ok=True)
        self.db=sqlite3.connect(database)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS demo_account (
              id INTEGER PRIMARY KEY CHECK(id=1), login INTEGER NOT NULL,
              mode TEXT NOT NULL,currency TEXT NOT NULL,server TEXT NOT NULL,
              generated_at INTEGER NOT NULL,balance TEXT NOT NULL,
              equity TEXT NOT NULL,margin TEXT NOT NULL,margin_free TEXT NOT NULL,
              floating_profit TEXT NOT NULL,positions INTEGER NOT NULL,orders INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS demo_positions (
              ticket INTEGER PRIMARY KEY, identifier INTEGER NOT NULL,
              symbol TEXT NOT NULL,type INTEGER NOT NULL,magic INTEGER NOT NULL,
              volume TEXT NOT NULL,price_open TEXT NOT NULL,sl TEXT NOT NULL,
              tp TEXT NOT NULL,price_current TEXT NOT NULL,profit TEXT NOT NULL,
              swap TEXT NOT NULL,time_msc INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS demo_deals (
              ticket INTEGER PRIMARY KEY,order_ticket INTEGER NOT NULL,
              position_id INTEGER NOT NULL,symbol TEXT NOT NULL,type INTEGER NOT NULL,
              entry INTEGER NOT NULL,magic INTEGER NOT NULL,time_msc INTEGER NOT NULL,
              volume TEXT NOT NULL,price TEXT NOT NULL,profit TEXT NOT NULL,
              commission TEXT NOT NULL,swap TEXT NOT NULL,fee TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS demo_notifications (
              deal_ticket INTEGER PRIMARY KEY,kind TEXT NOT NULL,
              message_id TEXT,sent_at TEXT);
            CREATE TABLE IF NOT EXISTS demo_meta (
              key TEXT PRIMARY KEY,value TEXT NOT NULL);
        """)
        self.db.commit()

    def ingest(self, common: Path, *, now=None):
        now=time.time() if now is None else now
        rows=read_csv(common/"sktrader_demo_account.csv",ACCOUNT_COLS)
        if len(rows)!=1:
            raise ValueError("missing/ambiguous broker account")
        account=rows[0]
        if (int(account["login"])!=self.expected_login or account["mode"]!="DEMO"
            or account["currency"]!="GBP" or account["server"]!=self.expected_server):
            raise ValueError("unexpected broker account identity")
        generated=int(account["generated_at"])
        if not 0<=now-generated<=120:
            raise ValueError("stale/future broker account telemetry")
        values=[amount(account[k]) for k in
                ("balance","equity","margin","margin_free","floating_profit")]
        if any(v<0 for v in values[:4]):
            raise ValueError("negative broker asset or margin amount")
        pos_count,order_count=int(account["positions"]),int(account["orders"])
        if min(pos_count,order_count)<0:
            raise ValueError("negative account count")
        positions=read_csv(common/"sktrader_demo_positions.csv",POSITION_COLS)
        deals=read_csv(common/"sktrader_demo_deals.csv",DEAL_COLS)
        if len(positions)!=pos_count:
            raise ValueError("position export count mismatch")
        seen_positions=set()
        for p in positions:
            ticket=int(p["ticket"])
            if ticket<=0 or ticket in seen_positions or int(p["time_msc"])<=0:
                raise ValueError("invalid/duplicate position")
            seen_positions.add(ticket)
            for k in ("volume","price_open","sl","tp","price_current","profit","swap"):
                amount(p[k])
        seen_deals=set()
        for d in deals:
            ticket=int(d["ticket"])
            if ticket<=0 or ticket in seen_deals or int(d["time_msc"])<=0:
                raise ValueError("invalid/duplicate broker deal")
            seen_deals.add(ticket)
            for k in ("volume","price","profit","commission","swap","fee"):
                amount(d[k])
        with self.db:
            count=self.db.execute("SELECT COUNT(*) FROM demo_deals").fetchone()[0]
            first=self.db.execute("SELECT value FROM demo_meta WHERE key='initialized'").fetchone() is None
            self.db.execute("INSERT OR REPLACE INTO demo_account VALUES (1,?,?,?,?,?,?,?,?,?,?,?,?)",
                            (self.expected_login,"DEMO","GBP",self.expected_server,generated,
                             *map(str,values),pos_count,order_count))
            self.db.execute("DELETE FROM demo_positions")
            for p in positions:
                self.db.execute("INSERT INTO demo_positions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                                tuple(p[k] for k in POSITION_COLS))
            for d in deals:
                result=self.db.execute(
                    "INSERT OR IGNORE INTO demo_deals VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    tuple(d[k] for k in DEAL_COLS))
                if result.rowcount and not first and int(d["magic"])==self.auroux_magic:
                    entry=int(d["entry"])
                    kind={0:"BROKER_FILL",1:"BROKER_CLOSE",2:"BROKER_REVERSAL",
                          3:"BROKER_CLOSE_BY"}.get(entry)
                    if kind and d["symbol"].startswith("XAUUSD"):
                        self.db.execute("INSERT OR IGNORE INTO demo_notifications "
                                        "(deal_ticket,kind) VALUES (?,?)",(int(d["ticket"]),kind))
            self.db.execute("INSERT OR IGNORE INTO demo_meta(key,value) "
                            "VALUES ('initialized','1')")
        return {"positions":len(positions),"deals":len(deals),
                "account_currency":"GBP","login":self.expected_login}

    def dashboard(self):
        a=self.db.execute("SELECT login,mode,currency,server,generated_at,balance,"
                          "equity,margin,margin_free,floating_profit,positions,orders "
                          "FROM demo_account WHERE id=1").fetchone()
        if not a:
            raise ValueError("no verified broker telemetry yet")
        stamp=datetime.fromtimestamp(a[4],timezone.utc)
        if (datetime.now(timezone.utc)-stamp).total_seconds()>120:
            raise ValueError("stale broker account telemetry")
        summary=(f"**Broker verified DEMO account** {a[0]} | {a[3]}\\n"
                 f"Balance: **£{amount(a[5]):,.2f}** | Equity: **£{amount(a[6]):,.2f}**\\n"
                 f"Floating: £{amount(a[9]):,.2f} | Margin: £{amount(a[7]):,.2f}\\n"
                 f"Free margin: £{amount(a[8]):,.2f} | Positions: {a[10]} | Orders: {a[11]}\\n"
                 f"Snapshot: {stamp.isoformat()}")
        active=self.db.execute("SELECT ticket,symbol,type,volume,price_open,sl,tp,"
                               "profit FROM demo_positions ORDER BY ticket").fetchall()
        fields=[]
        for ticket,symbol,kind,volume,entry,sl,tp,profit in active[:20]:
            fields.append({"name":f"{symbol} #{ticket}",
                           "value":f"{'BUY' if kind==0 else 'SELL'} {volume} lot @ {entry} | SL {sl} | TP {tp} | Floating £{amount(profit):,.2f}",
                           "inline":False})
        return {"embeds":[{"title":"DK Capital | AUROUX MT5 Demo (BROKER VERIFIED)",
                            "description":summary.replace("\\n","\n"),
                            "fields":fields,
                            "color":3066993}],
                "allowed_mentions":{"parse":[]}}

    def weekly_summary(self, now=None):
        """GBP broker deal totals for the currently open ISO week.

        The seven-day MQL exporter lookback makes this a provisional weekly
        view until long-term deal reconciliation is commissioned.
        """
        now=datetime.now(timezone.utc) if now is None else now
        year,week,_=now.isocalendar()
        deals=self.db.execute(
            "SELECT entry,time_msc,profit,commission,swap,fee FROM demo_deals "
            "WHERE magic=? AND symbol LIKE 'XAUUSD%'",
            (self.auroux_magic,)).fetchall()
        filtered=[]
        for entry,stamp,profit,commission,swap,fee in deals:
            if datetime.fromtimestamp(stamp/1000,timezone.utc).isocalendar()[:2] == (year,week):
                filtered.append((entry,*map(amount,(profit,commission,swap,fee))))
        gross=sum((d[1] for d in filtered),Decimal("0"))
        costs=sum((sum(d[2:]) for d in filtered),Decimal("0"))
        exits=[d for d in filtered if d[0] in (1,2,3)]
        return {"year":year,"week":week,"deals":len(filtered),
                "closing_deal_segments":len(exits),
                "gross_gbp":gross,"costs_gbp":costs,
                "net_gbp":gross+costs}

    def weekly_embed(self, now=None):
        s=self.weekly_summary(now)
        return {"embeds":[{"title":f"AUROUX Demo | Week {s['year']}-W{s['week']:02d} (BROKER DEALS)",
                           "description":(f"GBP gross realized: £{s['gross_gbp']:,.2f}\\n"
                                          f"Commission/swap/fees: £{s['costs_gbp']:,.2f}\\n"
                                          f"Net from exported deal records: **£{s['net_gbp']:,.2f}**\\n"
                                          f"Deals: {s['deals']} | Closing deal segments: {s['closing_deal_segments']}\\n"
                                          "Provisional: MT5 exporter includes 7 days of deal history; "
                                          "not a complete independent trade reconciliation."),
                           "color":3066993}],
                "allowed_mentions":{"parse":[]}}

    def pending(self):
        return list(self.db.execute(
            "SELECT n.deal_ticket,n.kind,d.symbol,d.volume,d.price,d.profit,"
            "d.commission,d.swap,d.fee,d.time_msc,d.order_ticket,d.position_id "
            "FROM demo_notifications n JOIN demo_deals d ON d.ticket=n.deal_ticket "
            "WHERE n.sent_at IS NULL ORDER BY d.time_msc,n.deal_ticket"))

    def confirmed(self, ticket, message_id):
        if not str(message_id).isdigit():
            raise ValueError("Discord confirmation requires message id")
        with self.db:
            self.db.execute("UPDATE demo_notifications SET message_id=?,sent_at=? "
                            "WHERE deal_ticket=?",
                            (str(message_id),datetime.now(timezone.utc).isoformat(),ticket))


def send_discord(token,channel,payload,*,message_id=None):
    method="PATCH" if message_id else "POST"
    url=f"https://discord.com/api/v10/channels/{channel}/messages"
    if message_id:
        url+="/"+str(message_id)
    req=urllib.request.Request(url,data=json.dumps(payload).encode(),method=method,
                               headers={"Authorization":"Bot "+token,
                                        "Content-Type":"application/json"})
    with urllib.request.urlopen(req,timeout=12) as res:
        result=json.load(res)
    if not str(result.get("id","")).isdigit():
        raise RuntimeError("Discord message was not acknowledged")
    return str(result["id"])


def run():
    p=argparse.ArgumentParser()
    p.add_argument("--common",type=Path,required=True)
    p.add_argument("--database",type=Path,required=True)
    p.add_argument("--login",type=int,required=True)
    p.add_argument("--server",default="MetaQuotes-Demo")
    p.add_argument("--token-file",type=Path,required=True)
    p.add_argument("--dashboard-channel",required=True)
    p.add_argument("--confirmations-channel",required=True)
    p.add_argument("--results-channel",required=True)
    p.add_argument("--weekly-channel",default="")
    p.add_argument("--alerts-channel",default="")
    args=p.parse_args()
    import stat
    if any(not x.isdigit() or len(x)<15 for x in
           (args.dashboard_channel,args.confirmations_channel,args.results_channel)):
        p.error("exact Discord destination IDs required")
    if any(x and (not x.isdigit() or len(x)<15) for x in (args.weekly_channel,args.alerts_channel))
        p.error("exact Discord destination IDs required")
    if args.token_file.stat().st_mode & (stat.S_IRWXG|stat.S_IRWXO):
        p.error("bot token file must be owner-only")
    token=args.token_file.read_text().strip()
    store=BrokerStore(args.database,expected_login=args.login,expected_server=args.server)
    last=0
    last_weekly=0
    while True:
        try:
            store.ingest(args.common)
            for ticket,kind,symbol,vol,price,profit,comm,swap,fee,stamp,order,position in store.pending():
                ch=args.confirmations_channel if kind=="BROKER_FILL" else args.results_channel
                payload={"content":(f"**AUROUX {kind.replace('_',' ')} | BROKER VERIFIED DEMO**\\n"
                                    f"Deal #{ticket} | Order #{order} | Position #{position}\\n"
                                    f"{symbol} {vol} lots @ {price}\\n"
                                    f"Realized £{profit} | Commission £{comm} | Swap £{swap} | Fee £{fee}"),
                         "allowed_mentions":{"parse":[]},
                         "nonce":f"auroux-broker-{ticket}","enforce_nonce":True}
                mid=send_discord(token,ch,payload)
                store.confirmed(ticket,mid)
            if time.monotonic()-last>30:
                payload=store.dashboard()
                row=store.db.execute("SELECT value FROM demo_meta WHERE key='dashboard_message_id'").fetchone()
                mid=row[0] if row else None
                try:
                    new_mid=send_discord(token,args.dashboard_channel,payload,message_id=mid)
                except urllib.error.HTTPError as e:
                    if e.code!=404 or mid is None:
                        raise
                    new_mid=send_discord(token,args.dashboard_channel,payload)
                with store.db:
                    store.db.execute("INSERT INTO demo_meta(key,value) VALUES('dashboard_message_id',?) "
                                     "ON CONFLICT(key) DO UPDATE SET value=excluded.value",(new_mid,))
                last=time.monotonic()
            if args.weekly_channel and time.monotonic()-last_weekly>600:
                week=store.weekly_summary()
                key=f"weekly_message_{week['year']}_{week['week']}"
                row=store.db.execute("SELECT value FROM demo_meta WHERE key=?",(key,)).fetchone()
                mid=row[0] if row else None
                try:
                    new_mid=send_discord(token,args.weekly_channel,store.weekly_embed(),message_id=mid)
                except urllib.error.HTTPError as e:
                    if e.code!=404 or not mid:
                        raise
                    new_mid=send_discord(token,args.weekly_channel,store.weekly_embed())
                with store.db:
                    store.db.execute("INSERT INTO demo_meta(key,value) VALUES (?,?) "
                                     "ON CONFLICT(key) DO UPDATE SET value=excluded.value",(key,new_mid))
                last_weekly=time.monotonic()
        except (OSError,ValueError,RuntimeError,sqlite3.Error,urllib.error.URLError) as e:
            print("broker reporting paused:",str(e)[:240],flush=True)
        time.sleep(2)


if __name__=="__main__":
    run()
