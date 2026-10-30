"""Regression coverage for XAUUSD-only mixed-scanner Discord routing."""
import json
from datetime import datetime, timezone
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sktrader.core import SnapshotError
from sktrader.multi_scanners import (
    SCANNER_CHANNELS, EXISTING_SIGNAL_CHANNELS, ScannerStore, discord_card, is_xauusd_card,
    load_channels, parse_scanner_card, setup_channels,
)
from sktrader.watcher import Config, _send_scanner_pending


def card(scanner, symbol="XAUUSD", age="2 minutes ago", timeframe="5M",
         direction="BUY", suffix="Point of Interest 4025.50000 Invalidation 4015.50000 Exit 1 4035.50000 Exit 2 4045.50000"):
    return f"{scanner} {age} {symbol} {timeframe} - {direction} {suffix}"


class MultiScannerTests(unittest.TestCase):
    def test_all_eight_sources_and_aliases(self):
        cases = {
            "QUANTUM": "QUANTUM",
            "TREND WAVE": "TREND WAVE",
            "AUROUX": "AUROUX",
            "NEXUS": "NEXUS",
            "SNIPERPRO": "SNIPER PRO",
            "SNIPER PRO": "SNIPER PRO",
            "WOLFX": "WOLF X",
            "WOLF X": "WOLF X",
            "HARMONICS": "HARMONICS",
            "PRIME": "PRIME",
        }
        for written, canonical in cases.items():
            with self.subTest(written=written):
                parsed = parse_scanner_card(card(written))
                self.assertEqual(parsed.scanner, canonical)
                self.assertEqual(parsed.symbol, "XAUUSD")
        self.assertEqual(set(SCANNER_CHANNELS), set(cases.values()))

    def test_signal_time_uses_recorded_detection_not_delivery(self):
        signal = parse_scanner_card(card("SNIPERPRO", age="2 minutes ago"))
        content = discord_card(signal, observed_at="2026-10-30T08:02:00+00:00")["embeds"][0]["description"]
        self.assertIn("Approx. JIFU time:", content)
        self.assertIn("<t:1793347200:F>", content)  # 08:00 UTC approximate
        self.assertIn("Detected by Sktrader:", content)
        self.assertIn("<t:1793347320:F>", content)  # 08:02 UTC actual
        self.assertIn("2 minutes ago", content)

    def test_coverage_records_multiline_jifu_cards_without_reposting(self):
        # JIFU's DOM cards contain line breaks between scanner and age.
        # Normalization must use whitespace, not a literal backslash-s.
        with tempfile.TemporaryDirectory() as folder:
            store = ScannerStore(Path(folder) / "signals.db")
            latest = [
                "AUROUX\\n2 minutes ago\\nXAUUSD\\n5M - BUY\\n"
                "Point of Interest 4025.50000\\nInvalidation 4015.50000\\n"
                "Exit 1 4035.50000\\nExit 2 4045.50000",
                "SNIPERPRO\\n2 minutes ago\\nUSDJPY\\n5M - SELL\\n"
                "Point of Interest 150.10000\\nExit 1 149.50000",
            ]
            latest = [x.replace("\\\\n", "\\n") for x in latest]
            count, baseline = store.observe(latest)
            self.assertTrue(baseline)
            self.assertEqual(count, 0)
            rows = store.coverage()
            self.assertIn("AUROUX", rows)
            self.assertIn("SNIPER PRO", rows)
            self.assertEqual(rows["AUROUX"]["last_signal_at"], None)
            self.assertEqual(store.pending(), [])
            count, baseline = store.observe(latest)
            self.assertFalse(baseline)
            self.assertEqual(count, 0)
            self.assertEqual(store.pending(), [])
            store.conn.close()

    def test_observation_timestamp_survives_restart(self):
        with tempfile.TemporaryDirectory() as temp:
            filename = Path(temp) / "signals.db"
            store = ScannerStore(filename)
            old = [card("PRIME", symbol="USDJPY")]
            store.observe(old)
            store.observe([card("SNIPERPRO", age="3 minutes ago")] + old)
            _, signal = store.pending()[0]
            self.assertIsNotNone(signal.observed_at)
            self.assertIn("Detected by Sktrader", discord_card(signal)["embeds"][0]["description"])
            observed = signal.observed_at
            store.conn.close()
            again = ScannerStore(filename)
            self.assertEqual(again.pending()[0][1].observed_at, observed)
            again.conn.close()

    def test_legacy_queued_signal_remains_compatible(self):
        signal = parse_scanner_card(card("NEXUS"))
        self.assertNotIn("Detected by Sktrader", discord_card(signal)["embeds"][0]["description"])

    def test_other_markets_are_excluded(self):
        for symbol in ("BTCUSDT", "XAGUSD", "NAS100USD", "USDJPY", "EURJPY"):
            self.assertFalse(is_xauusd_card(card("WOLFX", symbol=symbol)))
        self.assertFalse(is_xauusd_card(card("PRIME", symbol="XAUUSDm")))

    def test_variable_exits_and_missing_stop_are_preserved(self):
        prime = parse_scanner_card(card("PRIME", suffix="Point of Interest 4022.50000 Zone S0"))
        self.assertEqual(prime.fields, [
            {"name": "Point of Interest", "value": "4022.50000"},
            {"name": "Zone", "value": "S0"},
        ])
        wolf = parse_scanner_card(card(
            "WOLFX", direction="SELL",
            suffix="Point of Interest 4014.53000 Invalidation 4026.55000 "
                   "Exit 1 4002.51000 Exit 2 3978.47000 Exit 3 3954.43000",
        ))
        self.assertEqual([f["name"] for f in wolf.fields],
                         ["Point of Interest", "Invalidation", "Exit1", "Exit2", "Exit3"])
        self.assertEqual([f["name"] for f in discord_card(prime)["embeds"][0]["fields"]],
                         ["Point of Interest", "Zone"])

    def test_unrecognized_xauusd_scanner_fails_not_misroutes(self):
        with self.assertRaisesRegex(ValueError, "Unconfigured"):
            parse_scanner_card(card("FAKEBANK"))
        with self.assertRaisesRegex(ValueError, "Non-XAUUSD"):
            parse_scanner_card(card("AUROUX", symbol="XAGUSD"))

    def test_baseline_and_only_new_gold_cards_queued(self):
        with tempfile.TemporaryDirectory() as temp:
            store = ScannerStore(Path(temp) / "signals.db")
            old = [card("NEXUS", "EURJPY"), card("WOLFX", "USDJPY"),
                   card("HARMONICS", "BTCUSDT")]
            self.assertEqual(store.observe(old), (0, True))
            self.assertEqual(store.observe([card("SNIPERPRO")] + old), (1, False))
            self.assertEqual(store.pending()[0][1].scanner, "SNIPER PRO")
            self.assertEqual(store.observe([card("SNIPERPRO", age="3 minutes ago")] +
                                           [card("NEXUS", "EURJPY", age="3 minutes ago"),
                                            card("WOLFX", "USDJPY", age="3 minutes ago"),
                                            card("HARMONICS", "BTCUSDT", age="3 minutes ago")]),
                             (0, False))
            self.assertEqual(len(store.pending()), 1)
            store.conn.close()

    def test_same_prices_different_scanners_are_independent(self):
        with tempfile.TemporaryDirectory() as temp:
            store = ScannerStore(Path(temp) / "signals.db")
            old = [card("WOLFX", "USDCAD"), card("HARMONICS", "BTCUSDT")]
            self.assertEqual(store.observe(old), (0, True))
            incoming = [card("AUROUX", timeframe="30M"),
                        card("WOLFX", timeframe="5M"),
                        card("NEXUS", timeframe="1H")]
            self.assertEqual(store.observe(incoming + old), (3, False))
            self.assertEqual({signal.scanner for _, signal in store.pending()},
                             {"AUROUX", "WOLF X", "NEXUS"})
            store.conn.close()

    def test_malformed_new_gold_preserves_snapshot(self):
        with tempfile.TemporaryDirectory() as temp:
            store = ScannerStore(Path(temp) / "signals.db")
            old = [card("WOLFX", "USDCAD"), card("HARMONICS", "BTCUSDT")]
            self.assertEqual(store.observe(old), (0, True))
            with self.assertRaisesRegex(SnapshotError, "Unparseable"):
                store.observe([card("FAKEBANK")] + old)
            self.assertEqual(store.observe([card("AUROUX")] + old), (1, False))
            store.conn.close()

    def test_replay_once_and_idempotency(self):
        with tempfile.TemporaryDirectory() as temp:
            filename = Path(temp) / "signals.db"
            store = ScannerStore(filename)
            latest = [card("AUROUX"), card("WOLFX", "USDJPY")]
            archive = latest + [card("PRIME", suffix="Point of Interest 4000 Zone S0"),
                                card("HARMONICS", symbol="BTCUSDT")]
            self.assertEqual(store.replay_once(archive, newest=latest), (2, False))
            self.assertEqual(len(store.pending()), 2)
            self.assertEqual(store.observe(latest), (0, False))
            store.conn.close()
            again = ScannerStore(filename)
            self.assertEqual(again.replay_once(archive, newest=latest), (0, True))
            again.conn.close()

    def test_channel_map_enforces_all_distinct_and_existing_auroux_id(self):
        channel = 1565508564775075981
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "channels.json"
            with self.assertRaisesRegex(ValueError, "setup-channels"):
                load_channels(path, channel)
            data = {name: str(1600000000000000000 + i + 1)
                    for i, name in enumerate(SCANNER_CHANNELS)}
            data["AUROUX"] = str(channel)
            path.write_text(json.dumps(data))
            self.assertEqual(load_channels(path, channel)["AUROUX"], channel)
            data["NEXUS"] = str(channel)
            path.write_text(json.dumps(data))
            with self.assertRaisesRegex(ValueError, "overlapping"):
                load_channels(path, channel)

    def test_remap_uses_existing_channels_without_creating_any(self):
        channel = 1565508564775075981
        guild = "1200000000000000000"
        category = "1300000000000000000"
        requests = []
        all_channels = [
            {"id": str(channel) if scanner == "AUROUX"
                   else str(1600000000000000000 + i),
             "name": name, "parent_id": category, "type": 0}
            for i, (scanner, name) in enumerate(EXISTING_SIGNAL_CHANNELS.items())
        ]
        # These are the unwanted duplicates: never use them for routing.
        for i, name in enumerate(SCANNER_CHANNELS.values()):
            all_channels.append({
                "id": str(1700000000000000000 + i), "name": name,
                "parent_id": category, "type": 0,
            })
        all_channels.append({
            "id": "1800000000000000000", "name": "gws-signals",
            "parent_id": category, "type": 0,
        })
        def discord_request(_token, method, route, payload=None):
            requests.append((method, route))
            self.assertEqual(method, "GET")
            if route == f"/channels/{channel}":
                return {"guild_id": guild, "parent_id": category,
                        "name": "auroux-signals", "type": 0}
            self.assertEqual(route, f"/guilds/{guild}/channels")
            return all_channels

        with tempfile.TemporaryDirectory() as tmp:
            mapping = Path(tmp) / "scanner-channels.json"
            prior = {scanner: str(1700000000000000000 + i)
                     for i, scanner in enumerate(EXISTING_SIGNAL_CHANNELS)}
            prior["AUROUX"] = str(channel)
            mapping.write_text(json.dumps(prior))
            with patch("sktrader.multi_scanners._discord_json", side_effect=discord_request):
                channels = setup_channels("fake-token", channel, mapping)
                again = setup_channels("fake-token", channel, mapping)
            self.assertEqual(channels, again)
            self.assertEqual(channels["AUROUX"], channel)
            self.assertEqual(len(channels), 8)
            self.assertEqual(load_channels(mapping, channel), channels)
            self.assertEqual(mapping.stat().st_mode & 0o077, 0)
            self.assertEqual(
                json.loads(mapping.with_suffix(".legacy.json").read_text()), prior,
            )
            for scanner, expected_name in EXISTING_SIGNAL_CHANNELS.items():
                self.assertEqual(
                    next(c["name"] for c in all_channels if c["id"] == str(channels[scanner])),
                    expected_name,
                )
            self.assertTrue(all(method == "GET" for method, _ in requests))

    def test_remap_missing_channel_preserves_old_mapping(self):
        channel = 1565508564775075981
        guild = "1200000000000000000"
        category = "1300000000000000000"
        existing = [
            {"id": str(channel) if scanner == "AUROUX"
                   else str(1600000000000000000 + i),
             "name": name, "parent_id": category, "type": 0}
            for i, (scanner, name) in enumerate(EXISTING_SIGNAL_CHANNELS.items())
            if scanner != "PRIME"
        ]
        def discord_request(_token, method, route, payload=None):
            if route == f"/channels/{channel}":
                return {"guild_id": guild, "parent_id": category,
                        "name": "auroux-signals", "type": 0}
            return existing
        with tempfile.TemporaryDirectory() as tmp:
            mapping = Path(tmp) / "scanner-channels.json"
            previous = {"AUROUX": str(channel), "PRIME": "1600000000000000001"}
            mapping.write_text(json.dumps(previous))
            with patch("sktrader.multi_scanners._discord_json", side_effect=discord_request):
                with self.assertRaisesRegex(ValueError, "prime-signals"):
                    setup_channels("fake-token", channel, mapping)
            self.assertEqual(json.loads(mapping.read_text()), previous)
            self.assertFalse(mapping.with_suffix(".legacy.json").exists())

    def test_delivery_routes_by_scanner_without_crossposting(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ScannerStore(Path(tmp) / "signals.db")
            old = [card("WOLFX", "USDCAD"), card("HARMONICS", "BTCUSDT")]
            store.observe(old)
            store.observe([card("WOLFX"), card("AUROUX")] + old)
            channels = {key: 1600000000000000000 + i
                        for i, key in enumerate(SCANNER_CHANNELS)}
            config = Config.from_env(require_discord=False)
            with patch("sktrader.watcher._post_discord") as sending:
                _send_scanner_pending(store, config, channels)
            self.assertEqual(sending.call_count, 2)
            ids = {call.kwargs["channel_id"] for call in sending.call_args_list}
            self.assertEqual(ids, {channels["WOLF X"], channels["AUROUX"]})
            self.assertEqual(store.pending(), [])
            store.conn.close()


if __name__ == "__main__":
    unittest.main()
