import sqlite3
import tempfile
import unittest
from pathlib import Path
from sktrader.paper_portfolios import PaperPortfolios
from sktrader.paper_dashboard import dashboard, DashboardPublisher


class PaperDashboardTests(unittest.TestCase):
    def test_five_portfolio_snapshot_and_no_broker_fill_claim(self):
        with tempfile.TemporaryDirectory() as folder:
            engine=PaperPortfolios(Path(folder)/"paper.db")
            payload=dashboard(engine.db)
            embed=payload["embeds"][0]
            self.assertEqual(len(embed["fields"]),5)
            self.assertIn("SIMULATED",embed["title"])
            self.assertIn("not broker executions",embed["description"])
            for field in embed["fields"]:
                self.assertIn("$1,000.00",field["value"])
            DashboardPublisher(engine.db)
            self.assertIsNone(engine.db.execute(
                "SELECT value FROM dashboard_meta WHERE key='message_id'").fetchone())
            engine.db.close()


if __name__=="__main__":
    unittest.main()
