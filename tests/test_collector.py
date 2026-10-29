import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from sktrader.core import AlertError, EventStore, SnapshotError, new_prefix, parse_card
from unittest.mock import patch
from sktrader.watcher import browser_geometry, _alert_embed


BUY = """AUROUX
6 minutes ago
XAUUSD
30M - BUY
Point of Interest
4025.50000
Invalidation
4015.50000
Exit 1
4035.50000
Exit 2
4045.50000
Level
250 pips"""

SELL = """AUROUX
16 hours ago
XAUUSD
5M - SELL
Point of Interest
4049.50000
Invalidation
4059.50000
Exit 1
4039.50000
Exit 2
4029.50000
Level
250 pips"""

BUY500 = """AUROUX
2 days ago
XAUUSD
5M - BUY
Point of Interest
4000.50000
Invalidation
3975.50000
Exit 1
4025.50000
Exit 2
4050.50000
Level
500 pips"""


class BrowserSizeTests(unittest.TestCase):
    def test_default_fits_1280x800_vnc(self):
        with patch.dict("os.environ", {"SKTRADER_VIEWPORT_WIDTH": "1160", "SKTRADER_VIEWPORT_HEIGHT": "640"}):
            viewport, args = browser_geometry()
        self.assertEqual(viewport, {"width": 1160, "height": 640})
        self.assertIn("--window-size=1240,720", args)

    def test_1080p_isolated_vnc(self):
        with patch.dict("os.environ", {"SKTRADER_VIEWPORT_WIDTH": "1760", "SKTRADER_VIEWPORT_HEIGHT": "920"}):
            viewport, args = browser_geometry()
        self.assertEqual(viewport, {"width": 1760, "height": 920})
        self.assertIn("--window-size=1840,1000", args)

    def test_bad_geometry_rejected(self):
        with patch.dict("os.environ", {"SKTRADER_VIEWPORT_WIDTH": "6000"}):
            with self.assertRaises(ValueError):
                browser_geometry()


class ParserTests(unittest.TestCase):
    def test_buy(self):
        a = parse_card(BUY)
        self.assertEqual((a.side, a.timeframe, a.poi, a.invalidation, a.tp1, a.tp2, a.level),
                         ("BUY", "30M", "4025.50000", "4015.50000", "4035.50000", "4045.50000", 250))

    def test_sell(self):
        a = parse_card(SELL)
        self.assertEqual((a.side, a.timeframe, a.poi, a.tp2), ("SELL", "5M", "4049.50000", "4029.50000"))

    def test_level_500(self):
        self.assertEqual(parse_card(BUY500).level, 500)

    def test_age_change_is_not_new_identity(self):
        a = parse_card(BUY)
        b = parse_card(BUY.replace("6 minutes ago", "7 minutes ago"))
        self.assertEqual(a.fingerprint(), b.fingerprint())

    def test_distinct_timeframes_are_distinct(self):
        a = parse_card(BUY)
        b = parse_card(BUY.replace("30M - BUY", "5M - BUY"))
        self.assertNotEqual(a.fingerprint(), b.fingerprint())

    def test_bad_stop_is_rejected(self):
        with self.assertRaises(AlertError):
            parse_card(BUY.replace("4015.50000", "4030.50000"))

    def test_missing_tp_is_rejected(self):
        with self.assertRaises(AlertError):
            parse_card(BUY.replace("Exit 2", "Exit Two"))

    def test_non_xauusd_rejected(self):
        with self.assertRaises(AlertError):
            parse_card(BUY.replace("XAUUSD", "GBPUSD"))

    def test_discord_embed_marks_watch_only(self):
        x = _alert_embed(3, "2026-10-29T12:00:00Z", parse_card(SELL))
        self.assertIn("NO TRADE", x["embeds"][0]["footer"]["text"])
        self.assertEqual(x["allowed_mentions"]["parse"], [])


class SnapshotTests(unittest.TestCase):
    def test_unchanged(self):
        self.assertEqual(new_prefix(["A", "B"], ["A", "B"]), [])

    def test_insert(self):
        self.assertEqual(new_prefix(["A", "B", "C"], ["D", "A", "B", "C"]), [0])

    def test_insert_identical_levels(self):
        self.assertEqual(new_prefix(["A", "B"], ["A", "A", "B"]), [0])

    def test_multiple_same_entries(self):
        self.assertEqual(new_prefix(["A", "B"], ["C", "C", "A", "B"]), [0, 1])

    def test_missing_overlap_fails_closed(self):
        with self.assertRaises(SnapshotError):
            new_prefix(["A", "B"], ["C", "D"])

    def test_single_card_same_level_new(self):
        self.assertEqual(new_prefix(["A"], ["A", "A"]), [0])

    def test_empty_fails_closed(self):
        with self.assertRaises(SnapshotError):
            new_prefix(["A"], [])


class StoreTests(unittest.TestCase):
    def test_bootstrap_reopen_and_outbox(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "events.db"
            a, b, c = parse_card(BUY), parse_card(SELL), parse_card(BUY500)
            store = EventStore(path)
            count, baseline = store.observe([a, b])
            self.assertEqual((count, baseline), (0, True))
            count, baseline = store.observe([c, a, b])
            self.assertEqual((count, baseline), (1, False))
            self.assertEqual(store.pending()[0][2].level, 500)
            store.conn.close()
            # A restart must not replay events or reset snapshot.
            reopened = EventStore(path)
            self.assertEqual(reopened.observe([c, a, b]), (0, False))
            rows = reopened.pending()
            self.assertEqual(len(rows), 1)
            reopened.delivered(rows[0][0])
            self.assertEqual(reopened.pending(), [])
            reopened.conn.close()

    def test_transaction_keeps_state_when_overlap_untrusted(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "events.db"
            store = EventStore(path)
            a, b, c = parse_card(BUY), parse_card(SELL), parse_card(BUY500)
            store.observe([a, b])
            with self.assertRaises(SnapshotError):
                store.observe([c])
            self.assertEqual(store.observe([a, b]), (0, False))
            self.assertEqual(store.pending(), [])
            store.conn.close()


if __name__ == "__main__":
    unittest.main()
