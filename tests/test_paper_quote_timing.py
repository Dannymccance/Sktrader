import tempfile
import unittest
from pathlib import Path
from sktrader.paper_portfolios import PaperPortfolios


class SignalTimeTests(unittest.TestCase):
    def test_quote_before_signal_never_opens_paper_trade(self):
        with tempfile.TemporaryDirectory() as root:
            p=PaperPortfolios(Path(root)/"test.db")
            p.accept(101,{"scanner":"NEXUS","side":"BUY","symbol":"XAUUSD",
              "observed_at":"2026-10-30T12:00:05Z",
              "fields":[{"name":"Point of Interest","value":"4000"},
                        {"name":"Invalidation","value":"3990"},
                        {"name":"Exit1","value":"4010"}]},
              expires_at="2026-10-30T12:02:05Z")
            p.on_quote(observed_at="2026-10-30T12:00:04Z",bid=3999.5,ask=4000)
            self.assertEqual(p.db.execute(
                "SELECT state FROM paper_signals WHERE event_id=101").fetchone()[0],
                "PENDING")
            p.on_quote(observed_at="2026-10-30T12:00:06Z",bid=3999.5,ask=4000)
            self.assertEqual(p.db.execute(
                "SELECT state FROM paper_signals WHERE event_id=101").fetchone()[0],
                "OPEN")
            p.db.close()


if __name__=="__main__":
    unittest.main()
