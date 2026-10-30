import csv
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from sktrader.export_signals import records, write_exports
from sktrader.multi_scanners import ScannerStore


class ExportSignalsTests(unittest.TestCase):
    def test_archived_events_export_without_mutating_store(self):
        with tempfile.TemporaryDirectory() as d:
            folder = Path(d)
            db = folder / "events.sqlite"
            store = ScannerStore(db)
            with store.conn:
                store.conn.execute(
                    "INSERT INTO scanner_events(observed_at,payload,delivered_at) VALUES(?,?,?)",
                    ("2026-10-30T10:00:00+00:00", json.dumps({
                        "scanner": "AUROUX", "symbol": "XAUUSD", "timeframe": "5M",
                        "side": "BUY", "relative_age": "2 minutes ago",
                        "fields": [{"name": "Point of Interest", "value": "<4020>"},
                                   {"name": "Exit1", "value": "4030"}],
                    }), "2026-10-30T10:00:01+00:00"),
                )
            rows = records(db)
            paths = write_exports(rows, folder / "out")
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["source"], "AUROUX")
            self.assertIn("&lt;4020&gt;", paths[1].read_text())
            with paths[0].open(encoding="utf-8-sig", newline="") as fd:
                csv_rows = list(csv.DictReader(fd))
            self.assertEqual(csv_rows[0]["entry"], "<4020>")
            self.assertEqual(store.conn.execute("SELECT COUNT(*) FROM scanner_events").fetchone()[0], 1)
            store.conn.close()


if __name__ == "__main__":
    unittest.main()
