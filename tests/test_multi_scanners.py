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
    is_monitored_card, is_sniper_pro_card, ensure_sniper_symbol_channel,
    load_channels, parse_scanner_card, setup_channels,
)
from sktrader.watcher import Config, _send_scanner_pending


def card(scanner, symbol="XAUUSD", age="2 minutes ago", timeframe="5M",
         direction="BUY", suffix="Point of Interest 4025.50000 Invalidation 4015.50000 Exit 1 4035.50000 Exit 2 4045.50000"):
    return f"{scanner} {age} {symbol} {timeframe} - {direction} {suffix}"


class MultiScannerTests(unittest.TestCase):
    def test_dom_capture_does_not_require_numeric_poi_for_mixed_feed(self):
        # A non-XAUUSD scanner may use a zone instead of a numeric POI.
        # Excluding it breaks ordered overlap for every other scanner.
        from sktrader.watcher import EXTRACT_CARDS_JS
        self.assertIn("point of interest", EXTRACT_CARDS_JS)
        self.assertNotIn(r"Point of Interest\\s*[0-9]", EXTRACT_CARDS_JS)
        self.assertIn("BUY|SELL", EXTRACT_CARDS_JS)

    def test_partial_dom_extraction_accepts_newest_and_skips_older(self):
        from sktrader.watcher import _read_feed_cards, EXTRACT_CARDS_JS, DIAG_JS

        newest = card("AUROUX")
        older = card("NEXUS", symbol="USDJPY")

        class Page:
            def __init__(self, slots):
                self.slots = slots

            def evaluate(self, script):
                if script == EXTRACT_CARDS_JS:
                    return self.slots
                if script == DIAG_JS:
                    return {"poi_labels": 3}
                raise AssertionError("Unexpected script")

        self.assertIn("cards.push(captured)", EXTRACT_CARDS_JS)
        self.assertEqual(_read_feed_cards(Page([newest, None, older])), [newest, older])
        with self.assertRaisesRegex(SnapshotError, "Newest visible JIFU alert"):
            _read_feed_cards(Page([None, newest, older]))
        with self.assertRaisesRegex(SnapshotError, "Newest visible JIFU alert"):
            _read_feed_cards(Page([]))

    def test_missing_older_card_does_not_block_newest_signal(self):
        with tempfile.TemporaryDirectory() as temp:
            store = ScannerStore(Path(temp) / "signals.db")
            old = [
                card("NEXUS", symbol="USDJPY"),
                card("WOLF X", symbol="EURJPY"),
                card("PRIME", symbol="XAGUSD"),
                card("HARMONICS", symbol="BTCUSDT"),
            ]
            self.assertEqual(store.observe(old), (0, True))

            # The older WOLF X card fails DOM extraction, but AUROUX
            # has appeared at the top and two older cards still match.
            partial = [card("AUROUX"), old[0], old[2], old[3]]
            self.assertEqual(store.observe(partial), (1, False))
            self.assertEqual(store.pending()[0][1].scanner, "AUROUX")
            self.assertEqual(store.observe(partial), (0, False))
            self.assertEqual(len(store.pending()), 1)
            store.conn.close()

    def test_missing_older_card_does_not_create_false_new_event(self):
        with tempfile.TemporaryDirectory() as temp:
            store = ScannerStore(Path(temp) / "signals.db")
            old = [
                card("AUROUX"), card("NEXUS", symbol="USDJPY"),
                card("WOLFX", symbol="EURJPY"),
                card("PRIME", symbol="XAGUSD"),
            ]
            self.assertEqual(store.observe(old), (0, True))
            self.assertEqual(store.observe([old[0], old[2], old[3]]), (0, False))
            self.assertEqual(store.pending(), [])
            store.conn.close()

    def test_partial_feed_with_one_old_match_still_fails_closed(self):
        with tempfile.TemporaryDirectory() as temp:
            store = ScannerStore(Path(temp) / "signals.db")
            old = [card("NEXUS", symbol="USDJPY"), card("PRIME", symbol="XAGUSD")]
            store.observe(old)
            with self.assertRaisesRegex(SnapshotError, "overlap"):
                store.observe([card("AUROUX"), old[0]])
            self.assertEqual(store.pending(), [])
            store.conn.close()

    def test_live_newest_only_fallback_queues_once_and_records_gap(self):
        with tempfile.TemporaryDirectory() as temp:
            store = ScannerStore(Path(temp) / "signals.db")
            old = [
                card("PRIME", symbol="USDJPY"),
                card("NEXUS", symbol="EURJPY"),
                card("WOLFX", symbol="XAGUSD"),
            ]
            store.observe(old)
            newest = card("AUROUX", age="just now")

            # Ordinary callers retain strict overlap protection.
            with self.assertRaisesRegex(SnapshotError, "overlap"):
                store.observe([newest])
            # The live watch loop has verified an unfiltered JIFU sidebar:
            # accept only its latest card without inventing older history.
            self.assertEqual(store.observe([newest], allow_newest_only=True), (1, False))
            self.assertEqual(store.observe([newest], allow_newest_only=True), (0, False))
            self.assertEqual(len(store.pending()), 1)
            self.assertEqual(
                store.conn.execute("SELECT COUNT(*) FROM scanner_monitoring_gaps").fetchone()[0], 1
            )
            store.conn.close()

    def test_newest_only_does_not_repost_previously_seen_card(self):
        with tempfile.TemporaryDirectory() as temp:
            store = ScannerStore(Path(temp) / "signals.db")
            old = [card("AUROUX"), card("NEXUS", symbol="USDJPY")]
            store.observe(old)
            self.assertEqual(store.observe([old[0]], allow_newest_only=True), (0, False))
            self.assertEqual(store.observe([old[0]], allow_newest_only=True), (0, False))
            self.assertEqual(store.pending(), [])
            store.conn.close()

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
        self.assertIn("2026-10-30 08:02:00 UTC", content)  # 08:02:00 UTC actual
        self.assertIn("2 minutes ago", content)

    def test_signal_detection_shows_seconds_not_delivery_time(self):
        signal = parse_scanner_card(card("AUROUX", age="just now"))
        # Preserve the captured second and normalize a timezone offset to UTC.
        content = discord_card(
            signal, observed_at="2026-10-30T09:02:37.800+01:00"
        )["embeds"][0]["description"]
        self.assertIn("**Detected by Sktrader:**", content)
        self.assertIn("2026-10-30 08:02:37 UTC", content)
        self.assertNotIn("2026-10-30 08:02:00 UTC", content)

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
            latest = [x.replace("\\n", "\n") for x in latest]
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

    def test_gap_rebaseline_preserves_outbox_and_resumes_new_signals(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ScannerStore(Path(tmp) / "signals.db")
            old = [card("WOLFX", "USDCAD"), card("HARMONICS", "BTCUSDT")]
            self.assertEqual(store.observe(old), (0, True))
            # Old rows are still queued if Discord was unreachable.
            self.assertEqual(store.observe([card("NEXUS")] + old), (1, False))
            previous_pending = len(store.pending())
            replacement = [
                card("AUROUX", timeframe="1H", direction="SELL",
                     suffix="Point of Interest 3924.50000 Invalidation 3934.50000 "
                            "Exit 1 3914.50000 Exit 2 3904.50000"),
                card("PRIME", suffix="Point of Interest 4010 Zone S0"),
            ]
            with self.assertRaisesRegex(SnapshotError, "overlap"):
                store.observe(replacement)
            store.rebaseline_after_gap(replacement)
            self.assertEqual(len(store.pending()), previous_pending)
            self.assertEqual(store.observe(replacement), (0, False))
            self.assertEqual(store.observe([card("SNIPERPRO")] + replacement), (1, False))
            self.assertEqual(len(store.pending()), previous_pending + 1)
            gap = store.conn.execute(
                "SELECT previous_cards, replacement_cards "
                "FROM scanner_monitoring_gaps"
            ).fetchone()
            self.assertEqual(gap, (3, 2))
            store.conn.close()

    def test_gap_rebaseline_refuses_empty_and_valid_overlap(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ScannerStore(Path(tmp) / "signals.db")
            old = [card("AUROUX"), card("PRIME")]
            store.observe(old)
            with self.assertRaisesRegex(SnapshotError, "empty"):
                store.rebaseline_after_gap([])
            with self.assertRaisesRegex(SnapshotError, "overlap restored"):
                store.rebaseline_after_gap([card("SNIPERPRO")] + old)
            self.assertEqual(store.observe([card("SNIPERPRO")] + old), (1, False))
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


    def test_sniper_pro_non_gold_parsing_and_other_scanners_still_gold_only(self):
        for symbol, direction, timeframe, poi, stop, tp1, tp2 in [
            ("NAS100USD", "BUY", "15M", "27725.45000", "27625.45000", "27825.45000", "27925.45000"),
            ("US30USD", "BUY", "5M", "52160.40000", "52060.40000", "52260.40000", "52360.40000"),
            ("BTCUSDT", "SELL", "15M", "74074.16750", "74324.16750", "73824.16750", "73574.16750"),
        ]:
            with self.subTest(symbol=symbol):
                raw = card("SNIPERPRO", symbol=symbol, timeframe=timeframe, direction=direction,
                           suffix=f"Point of Interest {poi} Invalidation {stop} Exit 1 {tp1} Exit 2 {tp2}")
                self.assertTrue(is_sniper_pro_card(raw))
                self.assertTrue(is_monitored_card(raw))
                parsed = parse_scanner_card(raw)
                self.assertEqual((parsed.scanner, parsed.symbol, parsed.timeframe, parsed.side),
                                 ("SNIPER PRO", symbol, timeframe, direction))
                self.assertEqual([f["value"] for f in parsed.fields], [poi, stop, tp1, tp2])
                self.assertIn(f"**{symbol}**", discord_card(parsed)["embeds"][0]["description"])
        with self.assertRaisesRegex(ValueError, "Non-XAUUSD"):
            parse_scanner_card(card("AUROUX", symbol="BTCUSDT"))
        self.assertFalse(is_monitored_card(card("WOLFX", symbol="BTCUSDT")))

    def test_sniper_pro_all_markets_outbox_preserves_gold_feed(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ScannerStore(Path(tmp) / "events.db")
            previous = [card("NEXUS", symbol="EURJPY"), card("HARMONICS", symbol="BTCUSDT")]
            self.assertEqual(store.observe(previous), (0, True))
            newly_seen = [
                card("SNIPERPRO", symbol="NAS100USD"),
                card("SNIPER PRO", symbol="US30USD"),
                card("SNIPERPRO", symbol="BTCUSDT", direction="SELL"),
                card("SNIPERPRO", symbol="XAUUSD"),
                card("AUROUX", symbol="BTCUSDT"),
            ]
            self.assertEqual(store.observe(newly_seen + previous), (4, False))
            self.assertEqual({event.symbol for _, event in store.pending()},
                             {"NAS100USD", "US30USD", "BTCUSDT", "XAUUSD"})
            self.assertEqual(store.observe(newly_seen + previous), (0, False))
            store.conn.close()

    def test_sniper_channel_reuses_existing_then_creates_only_when_missing(self):
        anchor_id = 1565527578163552327
        guild_id = "1200000000000000000"
        category_id = "1300000000000000000"
        existing_id = "1700000000000000000"
        created_id = "1800000000000000000"
        calls = []
        def mock_request(token, method, route, payload=None):
            calls.append((method, route, payload))
            self.assertEqual(token, "mock-token")
            if method == "GET" and route == f"/channels/{anchor_id}":
                return {"name": "sniper-pro-signals", "type": 0,
                        "guild_id": guild_id, "parent_id": category_id}
            if method == "GET" and route == f"/guilds/{guild_id}/channels":
                return [{"name": "sniper-pro-nas100usd-signals", "id": existing_id,
                         "type": 0, "parent_id": category_id}]
            if method == "POST" and route == f"/guilds/{guild_id}/channels":
                self.assertEqual(payload["name"], "sniper-pro-btcusdt-signals")
                self.assertEqual(payload["parent_id"], category_id)
                return {"name": payload["name"], "id": created_id,
                        "type": 0, "parent_id": category_id}
            raise AssertionError(f"Unexpected Discord call: {method} {route}")
        with patch("sktrader.multi_scanners._discord_json", side_effect=mock_request):
            self.assertEqual(ensure_sniper_symbol_channel("mock-token", anchor_id, "XAUUSD"), anchor_id)
            self.assertEqual(ensure_sniper_symbol_channel("mock-token", anchor_id, "NAS100USD"), int(existing_id))
            self.assertEqual(ensure_sniper_symbol_channel("mock-token", anchor_id, "BTCUSDT"), int(created_id))
        self.assertEqual(sum(method == "POST" for method, _, _ in calls), 1)

    def test_sniper_routing_isolated_from_gold_execution_channel(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ScannerStore(Path(tmp) / "signals.db")
            baseline = [card("NEXUS", symbol="EURJPY")]
            store.observe(baseline)
            store.observe([card("SNIPERPRO", symbol="US30USD"),
                           card("SNIPERPRO", symbol="XAUUSD"),
                           card("AUROUX", symbol="XAUUSD")] + baseline)
            channels = {scanner: 1600000000000000000 + i
                        for i, scanner in enumerate(SCANNER_CHANNELS)}
            gold_channel = channels["SNIPER PRO"]
            dedicated_channel = 1900000000000000000
            config = Config.from_env(require_discord=False)
            with patch("sktrader.watcher.ensure_sniper_symbol_channel",
                       return_value=dedicated_channel) as ensure, \
                 patch("sktrader.watcher._bot_token", return_value="mock-token"), \
                 patch("sktrader.watcher._post_discord") as posting:
                _send_scanner_pending(store, config, channels)
            ensure.assert_called_once_with("mock-token", gold_channel, "US30USD")
            posted = {call.args[1]["embeds"][0]["description"].split("**")[1]: call.kwargs["channel_id"]
                      for call in posting.call_args_list}
            self.assertEqual(posted["US30USD"], dedicated_channel)
            self.assertEqual(posted["XAUUSD"], gold_channel)
            self.assertEqual(posting.call_count, 3)
            self.assertEqual(store.pending(), [])
            store.conn.close()


if __name__ == "__main__":
    unittest.main()
