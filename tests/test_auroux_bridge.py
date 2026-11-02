import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from datetime import datetime, timezone

from sktrader.auroux_bridge import export_lines, export_once, price_field, deadline_epoch, order_instruction


class AurouxBridgeTests(unittest.TestCase):
    @staticmethod
    def event(scanner="AUROUX", side="BUY", poi="4000.5", sl="3990", tp="4010"):
        return {
            "scanner": scanner, "symbol": "XAUUSD", "side": side,
            "observed_at": "2026-10-30T20:00:00+00:00", "relative_age": "just now",
            "fields": [
                {"name": "Point of Interest", "value": poi},
                {"name": "Invalidation", "value": sl},
                {"name": "Exit1", "value": tp},
            ],
        }

    def test_maps_a_single_auroux_event_to_mt5_levels(self):
        con = sqlite3.connect(":memory:")
        con.execute("CREATE TABLE scanner_events(id INTEGER PRIMARY KEY, payload TEXT)")
        con.execute("INSERT INTO scanner_events VALUES(?,?)", (21, json.dumps(self.event())))
        con.execute("INSERT INTO scanner_events VALUES(?,?)", (22, json.dumps(self.event(scanner="NEXUS"))))
        self.assertEqual(export_lines(con), [f"21;BUY;4000.50000000;3990.00000000;4010.00000000;{int(datetime(2026,10,30,20,2,1,tzinfo=timezone.utc).timestamp())};MARKET\n"])

    def test_market_default_and_explicit_pending(self):
        event = self.event()
        self.assertEqual(order_instruction(event), "MARKET")
        event["fields"].append({"name": "Order Type", "value": "BUY LIMIT"})
        self.assertEqual(order_instruction(event), "BUY_LIMIT")

    def test_invalid_pending_instructions_fail_closed(self):
        for instruction in ("SELL LIMIT", "UNKNOWN", "BUY STOP LIMIT"):
            with self.subTest(instruction=instruction):
                event = self.event()
                event["fields"].append({"name": "Order Type", "value": instruction})
                with self.assertRaises(ValueError):
                    order_instruction(event)

    def test_explicit_pending_written_to_bridge(self):
        con = sqlite3.connect(":memory:")
        con.execute("CREATE TABLE scanner_events(id INTEGER PRIMARY KEY, payload TEXT)")
        event = self.event()
        event["fields"].append({"name": "Order Type", "value": "BUY STOP"})
        con.execute("INSERT INTO scanner_events VALUES (?,?)", (99, json.dumps(event)))
        self.assertTrue(export_lines(con)[0].rstrip().endswith(";BUY_STOP"))

    def test_conservative_age_and_missing_timestamp(self):
        event = self.event()
        event["relative_age"] = "2 minutes ago"
        expected = int(datetime(2026, 10, 30, 20, 0, 1, tzinfo=timezone.utc).timestamp())
        self.assertEqual(deadline_epoch(event), expected)
        event.pop("observed_at")
        self.assertIsNone(deadline_epoch(event))

    def test_rejects_unusable_or_ambiguous_entry_prices(self):
        for text in ("4000-4005", "approx 4000", "", "4000 / 4001"):
            with self.subTest(text=text):
                with self.assertRaises(ValueError):
                    price_field([{"name": "Point of Interest", "value": text}], "Point of Interest")

    def test_export_repeatedly_does_not_duplicate_lines(self):
        with tempfile.TemporaryDirectory() as dirname:
            db = Path(dirname) / "signals.db"
            output = Path(dirname) / "auroux_signals.csv"
            con = sqlite3.connect(db)
            con.execute("CREATE TABLE scanner_events(id INTEGER PRIMARY KEY, payload TEXT)")
            con.execute("INSERT INTO scanner_events VALUES(?,?)", (30, json.dumps(self.event())))
            con.commit()
            con.close()
            self.assertEqual(export_once(db, output), 1)
            first = output.read_text()
            self.assertEqual(export_once(db, output), 1)
            self.assertEqual(output.read_text(), first)

    def test_rejects_reversed_stop_and_target(self):
        con = sqlite3.connect(":memory:")
        con.execute("CREATE TABLE scanner_events(id INTEGER PRIMARY KEY, payload TEXT)")
        con.execute("INSERT INTO scanner_events VALUES(?,?)", (1, json.dumps(self.event(sl="4010"))))
        self.assertEqual(export_lines(con), [])


if __name__ == "__main__":
    unittest.main()
