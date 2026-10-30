import csv
import tempfile
import unittest
from pathlib import Path
from sktrader.broker_reporting import BrokerStore,ACCOUNT_COLS,POSITION_COLS,DEAL_COLS


class BrokerTelemetryTests(unittest.TestCase):
    def setUp(self):
        self.t=tempfile.TemporaryDirectory()
        self.root=Path(self.t.name)
        self.store=BrokerStore(self.root/"broker.db",expected_login=5058169035)
        self.data={
            "sktrader_demo_account.csv":(ACCOUNT_COLS,[["5058169035","DEMO","GBP","MetaQuotes-Demo",
                "1793397000","3000","2998","12","2986","-2","1","0"]]),
            "sktrader_demo_positions.csv":(POSITION_COLS,[["1234","333","XAUUSD","0",
                 "2601030","0.01","4000","3990","4010","3998","-2","0","1793397000100"]]),
            "sktrader_demo_deals.csv":(DEAL_COLS,[["6543","7654","333","XAUUSD","0",
                "0","2601030","1793397000100","0.01","4000","0","-0.05","0","0"]])}
        self.write()

    def tearDown(self):
        self.store.db.close()
        self.t.cleanup()

    def write(self):
        for filename,(header,rows) in self.data.items():
            with (self.root/filename).open("w",newline="") as f:
                writer=csv.writer(f,delimiter=";")
                writer.writerow(header)
                writer.writerows(rows)

    def test_account_identity_and_no_replay_of_old_deals(self):
        result=self.store.ingest(self.root,now=1793397010)
        self.assertEqual(result["positions"],1)
        self.assertEqual(result["account_currency"],"GBP")
        self.assertEqual(self.store.pending(),[])
        self.store.ingest(self.root,now=1793397011)
        self.assertEqual(self.store.pending(),[])

    def test_real_account_is_rejected(self):
        self.data["sktrader_demo_account.csv"][1][0][1]="REAL"
        self.write()
        with self.assertRaisesRegex(ValueError,"unexpected broker"):
            self.store.ingest(self.root,now=1793397010)

    def test_broker_fills_require_new_verified_ticket(self):
        self.store.ingest(self.root,now=1793397010)
        self.data["sktrader_demo_deals.csv"][1].append(
            ["7777","9999","444","XAUUSD","0","0","2601030",
             "1793397001100","0.01","4001","0","-0.05","0","0"])
        self.write()
        self.store.ingest(self.root,now=1793397011)
        self.assertEqual([r[1] for r in self.store.pending()],["BROKER_FILL"])
        self.store.confirmed(7777,"123456789012345678")
        self.assertEqual(self.store.pending(),[])

    def test_no_other_magic_sends_auroux_fill(self):
        self.store.ingest(self.root,now=1793397010)
        self.data["sktrader_demo_deals.csv"][1].append(
            ["8888","9999","444","XAUUSD","0","0","0",
             "1793397001100","0.01","4001","0","0","0","0"])
        self.write()
        self.store.ingest(self.root,now=1793397011)
        self.assertEqual(self.store.pending(),[])

    def test_partial_position_export_rejected_without_overwriting(self):
        self.data["sktrader_demo_positions.csv"][1].clear()
        self.write()
        with self.assertRaisesRegex(ValueError,"count mismatch"):
            self.store.ingest(self.root,now=1793397010)
        self.assertEqual(self.store.db.execute(
            "SELECT COUNT(*) FROM demo_account").fetchone()[0],0)


if __name__=="__main__":
    unittest.main()
