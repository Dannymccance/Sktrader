import csv
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from datetime import datetime, timezone
from sktrader.paper_replay import PaperReplay, source_age_seconds


class ReplayTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.root=Path(self.temp.name)
        self.signal=self.root/"signals.db"
        self.market=self.root/"market.db"
        with sqlite3.connect(self.signal) as db:
            db.execute("CREATE TABLE scanner_events(id INTEGER PRIMARY KEY,observed_at TEXT,payload TEXT,delivered_at TEXT)")
        with sqlite3.connect(self.market) as db:
            db.execute("CREATE TABLE feed_identity(id INTEGER PRIMARY KEY,login INTEGER,mode TEXT,currency TEXT,server TEXT,symbol TEXT,contract_size TEXT,tick_size TEXT,tick_value TEXT,volume_min TEXT,volume_max TEXT,volume_step TEXT,generated_at INTEGER)")
            db.execute("CREATE TABLE feed_ticks(time_msc INTEGER,bid TEXT,ask TEXT,flags INTEGER,PRIMARY KEY(time_msc,bid,ask,flags))")
            db.execute("INSERT INTO feed_identity VALUES (1,5058169035,'DEMO','GBP','MetaQuotes-Demo','XAUUSD','100.0000','0.01','0.1','0.01','100','0.01',1793397000)")
        self.paper=self.root/"paper.db"

    def tearDown(self):
        self.temp.cleanup()

    def add(self,id=1,scanner="NEXUS",age="just now"):
        observed="2026-10-30T21:50:00+00:00"
        payload={"scanner":scanner,"symbol":"XAUUSD","side":"BUY","relative_age":age,
                 "fields":[{"name":"Point of Interest","value":"4000"},
                           {"name":"Invalidation","value":"3990"},
                           {"name":"Exit1","value":"4010"}]}
        with sqlite3.connect(self.signal) as db:
            db.execute("INSERT INTO scanner_events VALUES (?,?,?,NULL)",(id,observed,json.dumps(payload)))

    def test_age(self):
        self.assertEqual(source_age_seconds("just now"),0)
        self.assertEqual(source_age_seconds("2 minutes ago"),120)
        self.assertEqual(source_age_seconds("4 minutes ago"),240)
        self.assertIsNone(source_age_seconds("unavailable"))

    def test_bootstrap_does_not_backfill_old_signals(self):
        self.add()
        p=PaperReplay(self.signal,self.market,self.paper)
        self.assertEqual(p._state("signal_cursor"),1)
        self.assertEqual(p.pending_notifications(),[])
        p.engine.db.close();p.signals.close();p.market.close()

    def test_signal_and_paper_order_audit(self):
        p=PaperReplay(self.signal,self.market,self.paper)
        self.add()
        p.tick(now="2026-10-30T21:50:01Z")
        kinds=[e[3] for e in p.pending_notifications()]
        self.assertEqual(kinds,["SIGNAL_RECEIVED","ORDER_PENDING"])
        self.assertEqual(p.engine.snapshot()["NEXUS"]["balance_usd"],"1000")
        p.tick(now="2026-10-30T21:50:01Z")
        self.assertEqual(len(p.pending_notifications()),2)
        p.engine.db.close();p.signals.close();p.market.close()

    def test_market_data_gap_with_open_position_fails_closed(self):
        p=PaperReplay(self.signal,self.market,self.paper)
        self.add()
        p.tick(now="2026-10-30T21:50:01Z")
        p.engine.on_quote(observed_at="2026-10-30T21:50:02Z",bid="3999.8",ask="4000")
        self.assertEqual(p.engine.snapshot()["NEXUS"]["open_positions"],1)
        with sqlite3.connect(self.market) as db:
            db.execute("INSERT INTO feed_ticks VALUES (?,?,?,?)",
                       (1793397010000,"4010.2","4010.4",2))
        with self.assertRaisesRegex(RuntimeError,"market data gap"):
            p.tick(now="2026-10-30T21:50:11Z")
        self.assertEqual(p.engine.snapshot()["NEXUS"]["open_positions"],1)
        p.engine.db.close();p.signals.close();p.market.close()

    def test_old_signal_records_received_but_not_pending(self):
        p=PaperReplay(self.signal,self.market,self.paper)
        self.add(age="5 minutes ago")
        p.tick(now="2026-10-30T21:50:01Z")
        self.assertEqual([e[3] for e in p.pending_notifications()],
                         ["SIGNAL_RECEIVED","SIGNAL_SKIPPED"])
        p.engine.db.close();p.signals.close();p.market.close()


if __name__=="__main__":
    unittest.main()
