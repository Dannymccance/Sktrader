"""Regression tests for one-browser setup and safe JIFU catch-all routing."""
import io
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import call, patch

from sktrader import watcher
from sktrader.multi_scanners import (
    SCANNER_CHANNELS, ensure_other_signals_channel,
    ensure_sniper_symbol_channel, ScannerStore,
)


def card(source, symbol="XAUUSD", direction="BUY"):
    return (f"{source} 2 minutes ago {symbol} 5M - {direction} "
            "Point of Interest 100 Invalidation 90 Exit 1 120")


class CatchallTests(unittest.TestCase):
    def test_discord_channel_setup_does_not_acquire_browser_lock(self):
        config = SimpleNamespace(
            profile=Path("/tmp/sktrader-test-profile"),
            bot_token_file=Path("/tmp/sktrader-test-bot-token"),
        )
        mapping = {"SNIPER PRO": 1565527578163552327}
        with (
            patch.object(sys, "argv", ["sktrader", "setup-sniper-channels"]),
            patch.object(watcher.Config, "from_env", return_value=config),
            patch.object(watcher, "_one_browser_at_a_time", side_effect=AssertionError("browser lock acquired")) as guard,
            patch.object(watcher, "_scanner_channel_map", return_value=mapping),
            patch.object(watcher, "_bot_token", return_value="test-token"),
            patch.object(watcher, "ensure_sniper_symbol_channel", side_effect=[111, 222, 333]) as create,
            patch.object(watcher, "ensure_other_signals_channel", return_value=444) as other,
            patch("sys.stdout", new_callable=io.StringIO) as out,
        ):
            watcher.main()
        guard.assert_not_called()
        create.assert_has_calls([
            call("test-token", mapping["SNIPER PRO"], "NAS100USD"),
            call("test-token", mapping["SNIPER PRO"], "US30USD"),
            call("test-token", mapping["SNIPER PRO"], "BTCUSDT"),
        ])
        self.assertEqual(create.call_count, 3)
        other.assert_called_once_with("test-token", mapping["SNIPER PRO"])
        self.assertIn("OTHER JIFU SIGNALS", out.getvalue())

    def test_shared_channel_creates_once_and_reuses_existing_channel(self):
        anchor_id = 1565527578163552327
        guild_id = "1200000000000000000"
        category_id = "1300000000000000000"
        catchall_id = "1800000000000000000"
        state = []
        calls = []

        def discord_api(token, method, route, payload=None):
            calls.append((method, route))
            self.assertEqual(token, "test-token")
            if method == "GET" and route == f"/channels/{anchor_id}":
                return {"name": "sniper-pro-signals", "guild_id": guild_id,
                        "parent_id": category_id, "type": 0}
            if method == "GET" and route == f"/guilds/{guild_id}/channels":
                return list(state)
            if method == "POST" and route == f"/guilds/{guild_id}/channels":
                self.assertEqual(payload["name"], "jifu-other-signals")
                self.assertEqual(payload["parent_id"], category_id)
                new = {"name": payload["name"], "id": catchall_id,
                       "parent_id": category_id, "type": 0}
                state.append(new)
                return new
            raise AssertionError(f"Unexpected Discord call {method} {route}")

        with patch("sktrader.multi_scanners._discord_json", side_effect=discord_api):
            self.assertEqual(ensure_other_signals_channel("test-token", anchor_id), int(catchall_id))
            self.assertEqual(ensure_other_signals_channel("test-token", anchor_id), int(catchall_id))
            with self.assertRaisesRegex(ValueError, "shared other-signals"):
                ensure_sniper_symbol_channel("test-token", anchor_id, "EURJPY")
        self.assertEqual(sum(method == "POST" for method, _ in calls), 1)

    def test_every_signal_delivered_without_unknown_entering_gold_channel(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ScannerStore(Path(tmp) / "signals.db")
            baseline = [card("NEXUS", "EURJPY", "SELL")]
            self.assertEqual(store.observe(baseline), (0, True))
            incoming = [
                card("MYSTERY ALGO", "XAUUSD"),
                card("SNIPERPRO", "EURJPY"),
                card("QUANTUM", "XAGUSD"),
                card("SNIPERPRO", "BTCUSDT", "SELL"),
                card("SNIPERPRO", "XAUUSD"),
                card("AUROUX", "XAUUSD"),
            ]
            self.assertEqual(store.observe(incoming + baseline), (6, False))
            channels = {key: 1600000000000000000 + i
                        for i, key in enumerate(SCANNER_CHANNELS)}
            gold_channel = channels["SNIPER PRO"]
            dedicated_channel = 1900000000000000000
            other_channel = 2000000000000000000
            config = watcher.Config.from_env(require_discord=False)
            with (
                patch.object(watcher, "_bot_token", return_value="mock-token"),
                patch.object(watcher, "ensure_sniper_symbol_channel",
                             return_value=dedicated_channel) as dedicated,
                patch.object(watcher, "ensure_other_signals_channel",
                             return_value=other_channel) as other,
                patch.object(watcher, "_post_discord") as posted,
            ):
                watcher._send_scanner_pending(store, config, channels)
            self.assertEqual(posted.call_count, 6)
            self.assertEqual(store.pending(), [])
            destinations = {}
            for called in posted.call_args_list:
                fields = called.args[1]["embeds"][0]
                destinations[(fields["title"], fields["description"].split("\n\n**")[1].split("**")[0])] = called.kwargs["channel_id"]
            self.assertEqual(destinations[("SNIPER PRO", "XAUUSD")], gold_channel)
            self.assertEqual(destinations[("AUROUX", "XAUUSD")], channels["AUROUX"])
            self.assertEqual(destinations[("SNIPER PRO", "BTCUSDT")], dedicated_channel)
            self.assertEqual(destinations[("SNIPER PRO", "EURJPY")], other_channel)
            self.assertEqual(destinations[("QUANTUM", "XAGUSD")], other_channel)
            self.assertEqual(destinations[("MYSTERY ALGO", "XAUUSD")], other_channel)
            dedicated.assert_called_once_with("mock-token", gold_channel, "BTCUSDT")
            other.assert_called_once_with("mock-token", gold_channel)
            store.conn.close()


if __name__ == "__main__":
    unittest.main()
