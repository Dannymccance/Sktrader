import tempfile
import unittest
from pathlib import Path
from sktrader.paper_portfolios import PaperPortfolios, SCANNERS


def signal(scanner="NEXUS", side="BUY", fields=None):
    return {
        "scanner": scanner, "symbol": "XAUUSD", "side": side,
        "observed_at": "2026-10-30T12:00:00+00:00",
        "fields": fields if fields is not None else [
            {"name": "Point of Interest", "value": "4000"},
            {"name": "Invalidation", "value": "3990"},
            {"name": "Exit1", "value": "4010"},
        ],
    }


class FivePaperPortfolioTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = Path(self.temp.name) / "paper.db"
        self.engine = PaperPortfolios(self.db)

    def tearDown(self):
        self.engine.db.close()
        self.temp.cleanup()

    def test_exact_five_independent_balances(self):
        self.assertEqual(set(self.engine.snapshot()), set(SCANNERS))
        self.assertTrue(all(x["balance_usd"] == "1000" for x in
                            self.engine.snapshot().values()))

    def test_deduplication_and_restart(self):
        s = signal()
        self.assertEqual(self.engine.accept(3, s, expires_at="2026-10-30T12:02:00Z"), "PENDING")
        self.assertEqual(self.engine.accept(3, s, expires_at="2026-10-30T12:02:00Z"), "PENDING")
        self.engine.db.close()
        self.engine = PaperPortfolios(self.db)
        self.assertEqual(self.engine.db.execute(
            "SELECT COUNT(*) FROM paper_signals").fetchone()[0], 1)

    def test_missing_stop_is_skipped_not_invented(self):
        s = signal(scanner="PRIME", fields=[
            {"name": "Point of Interest", "value": "4000"},
            {"name": "Zone", "value": "S0"},
        ])
        self.assertEqual(self.engine.accept(4,s, expires_at="2026-10-30T12:02:00Z"),
                         "SKIPPED")

    def test_valid_quote_entry_and_tp1(self):
        self.engine.accept(5, signal(), expires_at="2026-10-30T12:02:00Z")
        self.engine.on_quote(observed_at="2026-10-30T12:00:01Z", bid=3999.7, ask=4000.2)
        self.assertEqual(self.engine.db.execute(
            "SELECT state FROM paper_signals WHERE event_id=5").fetchone()[0], "PENDING")
        self.engine.on_quote(observed_at="2026-10-30T12:00:02Z", bid=3999.7, ask=4000)
        self.assertEqual(self.engine.snapshot()["NEXUS"]["open_positions"], 1)
        self.engine.on_quote(observed_at="2026-10-30T12:00:03Z", bid=4010.2, ask=4010.4)
        self.assertEqual(self.engine.db.execute(
            "SELECT state FROM paper_signals WHERE event_id=5").fetchone()[0], "TP1")
        self.assertEqual(self.engine.snapshot()["NEXUS"]["open_positions"], 0)
        self.assertEqual(self.engine.snapshot()["PRIME"]["balance_usd"], "1000")

    def test_expiry_and_stale_ticks(self):
        self.engine.accept(6, signal(scanner="WOLF X"),
                           expires_at="2026-10-30T12:00:05Z")
        self.engine.on_quote(observed_at="2026-10-30T12:00:06Z", bid=3999, ask=3999.2)
        self.assertEqual(self.engine.db.execute(
            "SELECT state FROM paper_signals WHERE event_id=6").fetchone()[0], "EXPIRED")
        with self.assertRaisesRegex(ValueError,"out-of-order"):
            self.engine.on_quote(observed_at="2026-10-30T12:00:05Z", bid=3999, ask=4000)

    def test_excluded_group_does_not_create_account(self):
        self.assertEqual(self.engine.accept(7, signal(scanner="AUROUX"),
                         expires_at="2026-10-30T12:02:00Z"), "EXCLUDED")
        self.assertEqual(len(self.engine.snapshot()),5)

    def test_oversized_risk_is_not_rounded_up(self):
        self.engine.accept(8, signal(), expires_at="2026-10-30T12:02:00Z")
        self.engine.on_quote(observed_at="2026-10-30T12:00:01Z", bid=3999.5, ask=4000)
        volume = self.engine.db.execute(
            "SELECT lots FROM paper_signals WHERE event_id=8").fetchone()[0]
        self.assertEqual(volume, "0.04")


if __name__ == "__main__":
    unittest.main()
