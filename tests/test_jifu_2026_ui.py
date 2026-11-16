"""Regression checks for JIFU's November 2026 Alerts sidebar redesign.

The screenshot shows Alerts > Scanner alerts > All scanners / No filter,
a compact '5M - SELL' direction badge, and up to five exits.
"""
import tempfile
import unittest
from pathlib import Path

from sktrader.multi_scanners import ScannerStore, parse_scanner_card
from sktrader.watcher import EXTRACT_CARDS_JS, SIDEBAR_SCANNER_STATE_JS


NEW_TRENDWAVE = """TRENDWAVE
just now
BTCUSD
5M - SELL
Point of Interest
64368.00000
Invalidation
65100.00000
Exit 1
63981.87053
Exit 2
63595.74105
Exit 3
63209.61158
Exit 4
62823.48211
Exit 5
62437.35263"""

NEW_WOLFX = """WOLFX
just now
XAUUSD
1M - BUY
Point of Interest
3864.38000
Invalidation
3858.78000
Exit 1
3869.98000
Exit 2
3881.18000
Exit 3
3892.38000"""


class Jifu2026UiTests(unittest.TestCase):
    def test_unfiltered_dropdown_is_recognised_not_clicked(self):
        # Preserve legacy support and fail closed for unidentified selectors.
        self.assertIn("Select Scanner", SIDEBAR_SCANNER_STATE_JS)
        self.assertIn("All scanners", SIDEBAR_SCANNER_STATE_JS)
        self.assertIn("No filter", SIDEBAR_SCANNER_STATE_JS)
        self.assertIn("window.innerWidth * 0.60", SIDEBAR_SCANNER_STATE_JS)
        self.assertIn("foundUnselected ? 'unselected'", SIDEBAR_SCANNER_STATE_JS)
        self.assertNotIn(".click(", SIDEBAR_SCANNER_STATE_JS)

    def test_card_extraction_keeps_direction_and_poi_requirements(self):
        self.assertIn("point of interest", EXTRACT_CARDS_JS)
        self.assertIn("BUY|SELL", EXTRACT_CARDS_JS)

    def test_new_trendwave_card_preserves_all_five_exits(self):
        parsed = parse_scanner_card(NEW_TRENDWAVE)
        self.assertEqual((parsed.scanner, parsed.symbol, parsed.timeframe, parsed.side),
                         ("TREND WAVE", "BTCUSD", "5M", "SELL"))
        values = {f["name"]: f["value"] for f in parsed.fields}
        self.assertEqual(values["Point of Interest"], "64368.00000")
        self.assertEqual(values["Invalidation"], "65100.00000")
        self.assertEqual(values["Exit5"], "62437.35263")
        self.assertEqual(len(values), 7)

    def test_new_wolfx_badge_and_three_exits(self):
        parsed = parse_scanner_card(NEW_WOLFX)
        self.assertEqual((parsed.scanner, parsed.symbol, parsed.timeframe, parsed.side),
                         ("WOLF X", "XAUUSD", "1M", "BUY"))
        values = {f["name"]: f["value"] for f in parsed.fields}
        self.assertEqual(values["Exit3"], "3892.38000")

    def test_long_outage_baselines_without_posting_old_alerts(self):
        from datetime import datetime, timedelta, timezone

        with tempfile.TemporaryDirectory() as folder:
            store = ScannerStore(Path(folder) / "signals.db")
            store.observe([NEW_WOLFX])
            old_seen = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
            with store.conn:
                store.conn.execute(
                    "INSERT INTO scanner_coverage(scanner,last_seen_at) VALUES(?,?)",
                    ("WOLF X", old_seen),
                )
            self.assertTrue(store.stale_after_outage())
            store.baseline_after_outage([NEW_TRENDWAVE, NEW_WOLFX])
            self.assertEqual(store.pending(), [])
            self.assertEqual(store.observe([NEW_TRENDWAVE, NEW_WOLFX]), (0, False))
            recent = NEW_WOLFX.replace("3864.38000", "3865.38000")
            self.assertEqual(
                store.observe([recent, NEW_TRENDWAVE, NEW_WOLFX]), (1, False)
            )
            self.assertEqual(len(store.pending()), 1)
            store.conn.close()

    def test_fresh_feed_does_not_force_rebaseline(self):
        with tempfile.TemporaryDirectory() as folder:
            store = ScannerStore(Path(folder) / "signals.db")
            store.observe([NEW_WOLFX])
            with store.conn:
                store.conn.execute(
                    "INSERT INTO scanner_coverage(scanner,last_seen_at) VALUES(?,?)",
                    ("WOLF X", __import__("datetime").datetime.now(
                        __import__("datetime").timezone.utc).isoformat()),
                )
            self.assertFalse(store.stale_after_outage())
            store.conn.close()

    def test_identical_cards_are_not_reposted(self):
        with tempfile.TemporaryDirectory() as folder:
            store = ScannerStore(Path(folder) / "signals.db")
            self.assertEqual(store.observe([NEW_WOLFX]), (0, True))
            self.assertEqual(store.observe([NEW_TRENDWAVE, NEW_WOLFX]), (1, False))
            self.assertEqual(store.observe([NEW_TRENDWAVE, NEW_WOLFX]), (0, False))
            self.assertEqual(len(store.pending()), 1)
            store.conn.close()


if __name__ == "__main__":
    unittest.main()
