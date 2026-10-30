import csv
import os
from pathlib import Path
import tempfile
import unittest
from sktrader.market_feed import MarketFeed


class MarketFeedTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.feed = MarketFeed(self.root/"data.db", expected_login=12345)
        self.headers = {
            "sktrader_xau_identity.csv": ["login","mode","currency","server","symbol","contract_size","tick_size","tick_value","volume_min","volume_max","volume_step","generated_at"],
            "sktrader_xau_quotes.csv": ["time_msc","bid","ask","flags"],
            "sktrader_xau_m1.csv": ["time","open","high","low","close","tick_volume","spread"],
        }
        self.write("sktrader_xau_identity.csv",[["12345","DEMO","USD","Vantage-Demo","XAUUSD+","100","0.01","1","0.01","50","0.01","1793380000"]])
        self.write("sktrader_xau_quotes.csv",[["1793380000100","4000.1","4000.3","2"]])
        self.write("sktrader_xau_m1.csv",[["1793379960","3999","4002","3998","4001","150","20"]])
        os.utime(self.root/"sktrader_xau_quotes.csv", (1793380010,1793380010))

    def tearDown(self):
        self.feed.db.close()
        self.temp.cleanup()

    def write(self, name, rows):
        with (self.root/name).open("w",newline="") as f:
            writer=csv.writer(f,delimiter=";")
            writer.writerow(self.headers[name])
            writer.writerows(rows)

    def test_identity_and_duplicate_ingestion(self):
        self.assertEqual(self.feed.ingest(self.root, now=1793380010),{"ticks":1,"m1":1})
        self.assertEqual(self.feed.ingest(self.root, now=1793380011),{"ticks":1,"m1":1})

    def test_refuse_stale_tick_export_with_fresh_identity(self):
        os.utime(self.root/"sktrader_xau_quotes.csv", (1793380000,1793380000))
        with self.assertRaisesRegex(ValueError,"quote export stale"):
            self.feed.ingest(self.root, now=1793380050)
        self.assertEqual(self.feed.db.execute("SELECT COUNT(*) FROM feed_ticks").fetchone()[0],0)

    def test_refuse_future_broker_tick(self):
        self.write("sktrader_xau_quotes.csv",[["1793380800000","4000","4001","2"]])
        os.utime(self.root/"sktrader_xau_quotes.csv", (1793380010,1793380010))
        with self.assertRaisesRegex(ValueError,"future broker tick"):
            self.feed.ingest(self.root, now=1793380010)

    def test_refuse_real_account(self):
        self.write("sktrader_xau_identity.csv",[["12345","REAL","USD","Vantage-Live","XAUUSD+","100","0.01","1","0.01","50","0.01","1793380000"]])
        with self.assertRaisesRegex(ValueError,"broker profile mismatch"):
            self.feed.ingest(self.root, now=1793380010)

    def test_refuse_wrong_login(self):
        self.write("sktrader_xau_identity.csv",[["2","DEMO","USD","Vantage-Demo","XAUUSD+","100","0.01","1","0.01","50","0.01","1793380000"]])
        with self.assertRaisesRegex(ValueError,"broker profile mismatch"):
            self.feed.ingest(self.root, now=1793380010)

    def test_reject_stale(self):
        with self.assertRaisesRegex(ValueError,"stale"):
            self.feed.ingest(self.root, now=1793381000)

    def test_reject_crossed_quotes(self):
        self.write("sktrader_xau_quotes.csv",[["1793380000100","4001","4000","2"]])
        os.utime(self.root/"sktrader_xau_quotes.csv", (1793380010,1793380010))
        with self.assertRaisesRegex(ValueError,"invalid/future broker tick"):
            self.feed.ingest(self.root, now=1793380010)
        self.assertEqual(self.feed.db.execute("SELECT COUNT(*) FROM feed_ticks").fetchone()[0],0)


if __name__ == "__main__":
    unittest.main()
