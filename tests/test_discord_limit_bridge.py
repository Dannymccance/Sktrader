import json
import subprocess
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path

from sktrader.discord_limit_bridge import (
    CHANNEL_ID, DISCORD_EPOCH_MS, LIFETIME_SECONDS,
    SignalStore, extract_signal, message_time_ms,
)


def snowflake(timestamp_ms: int) -> str:
    return str(((timestamp_ms - DISCORD_EPOCH_MS) << 22) | 12345)


class DiscordLimitBridgeTests(unittest.TestCase):
    def setUp(self):
        self.now_ms = int(time.time() * 1000)
        self.author = "1565500000000000000"
        self.message = {
            "id": snowflake(self.now_ms - 500),
            "channel_id": CHANNEL_ID,
            "author": {"id": self.author, "bot": True},
            "content": "",
            "embeds": [{
                "title": "SNIPER PRO",
                "description": "just now\n\n**XAUUSD**\n**15M - BUY**",
                "fields": [
                    {"name": "Point of Interest", "value": "3905.05000"},
                    {"name": "Invalidation", "value": "3902.55000"},
                    {"name": "Exit1", "value": "3915.05000"},
                ],
            }],
        }

    def test_channel_and_author_exact(self):
        self.assertIsNotNone(extract_signal(self.message, channel_id=CHANNEL_ID,
                                            author_id=self.author, now_ms=self.now_ms))
        self.message["channel_id"] = "1565527578163552328"
        self.assertIsNone(extract_signal(self.message, channel_id=CHANNEL_ID,
                                         author_id=self.author, now_ms=self.now_ms))
        self.message["channel_id"] = CHANNEL_ID
        self.assertIsNone(extract_signal(self.message, channel_id=CHANNEL_ID,
                                         author_id="1565500000000000001", now_ms=self.now_ms))

    def test_price_direction_and_30m_expiry(self):
        signal = extract_signal(self.message, channel_id=CHANNEL_ID,
                                author_id=self.author, now_ms=self.now_ms)
        self.assertEqual(signal[1], "BUY")
        self.assertEqual(signal[2], "3905.05000")
        self.assertEqual(signal[4]-signal[3], LIFETIME_SECONDS)
        self.assertEqual(message_time_ms(signal[0]), self.now_ms-500)

    def test_old_message_fails_closed(self):
        self.message["id"] = snowflake(self.now_ms - 1800_001)
        self.assertIsNone(extract_signal(self.message, channel_id=CHANNEL_ID,
                                         author_id=self.author, now_ms=self.now_ms))

    def test_ignore_all_other_instructions_and_symbols(self):
        self.message["embeds"][0]["description"] = "XAGUSD 15M - BUY"
        self.assertIsNone(extract_signal(self.message, channel_id=CHANNEL_ID,
                                         author_id=self.author, now_ms=self.now_ms))
        self.message["embeds"][0]["description"] = "XAUUSD 15M - SELL"
        signal = extract_signal(self.message, channel_id=CHANNEL_ID,
                                author_id=self.author, now_ms=self.now_ms)
        self.assertEqual(signal[1], "SELL")
        self.message["embeds"][0]["fields"].append(
            {"name": "Point of Interest", "value": "3906"})
        self.assertIsNone(extract_signal(self.message, channel_id=CHANNEL_ID,
                                         author_id=self.author, now_ms=self.now_ms))

    def test_content_message(self):
        self.message["embeds"] = []
        self.message["content"] = "**XAUUSD** **5M - SELL**\n**Point of Interest**\n3905.05"
        signal = extract_signal(self.message, channel_id=CHANNEL_ID,
                                author_id=self.author, now_ms=self.now_ms)
        self.assertEqual(signal[1:3], ("SELL", "3905.05"))

    def test_persistence_and_no_duplicates(self):
        with tempfile.TemporaryDirectory() as td:
            db=Path(td)/"message.db"
            out=Path(td)/"MT5"/"signals.csv"
            signal=extract_signal(self.message, channel_id=CHANNEL_ID,
                                  author_id=self.author, now_ms=self.now_ms)
            store=SignalStore(db,out)
            self.assertTrue(store.add(signal,now_ms=self.now_ms))
            self.assertFalse(store.add(signal,now_ms=self.now_ms))
            row=out.read_text().strip().split(";")
            self.assertEqual(len(row),5)
            self.assertEqual(row[:3],list(signal[:3]))
            self.assertEqual(int(row[4])-int(row[3]),1800)
            store.con.close()
            reopened=SignalStore(db,out)
            self.assertEqual(reopened.con.execute(
                "SELECT COUNT(*) FROM limit_messages").fetchone()[0],1)
            reopened.con.close()

    def test_compile_staging_script_isolated(self):
        script=Path(__file__).resolve().parents[1]/"deploy"/"stage-discord-limit-30m-ea.sh"
        subprocess.run(["bash","-n",str(script)],check=True,capture_output=True)
        contents=script.read_text()
        self.assertIn("dkcapital-mt5-compiler-vnc", contents)
        self.assertIn("DiscordChannelLimit30m.mq5", contents)
        self.assertIn("5058169035", contents)
        self.assertIn("No existing MT5 service", contents)
        self.assertNotIn("systemctl restart", contents)
        self.assertNotIn("systemctl stop", contents)

    def test_no_market_fallback_in_ea(self):
        ea=(Path(__file__).resolve().parents[1]/"mt5"/"DiscordChannelLimit30m.mq5").read_text()
        self.assertIn("trade.BuyLimit(",ea)
        self.assertIn("trade.SellLimit(",ea)
        self.assertNotIn("trade.Buy(",ea)
        self.assertNotIn("trade.Sell(",ea)
        self.assertNotIn("trade.BuyStop(",ea)
        self.assertNotIn("trade.SellStop(",ea)
        self.assertIn("balance*0.04",ea)
        self.assertIn("MathFloor(requested/step",ea)
        self.assertIn("entry+(buy?-7.50:7.50)",ea)
        self.assertIn("entry+(buy?15.0:-15.0)",ea)
        self.assertIn("MathAbs(MathAbs(entry-sl)-7.50)>1e-6",ea)
        self.assertIn("MathAbs(MathAbs(tp-entry)-15.0)>1e-6",ea)
        self.assertIn("4% balance risk per trade, $7.50 SL, $15 TP, 30m expiry, unrestricted overlap",ea)
        self.assertIn("if(AlreadyOrdered(id)) return MarkHandled(id);",ea)
        self.assertNotIn("HasOpenAuroux",ea)
        self.assertNotIn("HasOpenLimit",ea)
        self.assertNotIn("MaxPortfolioRisk",ea)
        self.assertIn("expires-issued!=1800",ea)
        self.assertIn("ORDER_TIME_SPECIFIED",ea)
        self.assertIn("EventSetMillisecondTimer(PollMilliseconds)",ea)
        self.assertIn("ACCOUNT_TRADE_MODE_DEMO",ea)


if __name__ == "__main__":
    unittest.main()
