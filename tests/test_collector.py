import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
import os

from sktrader.core import AlertError, EventStore, SnapshotError, auroux_xauusd_card, feed_fingerprint, merge_history_page, new_prefix, parse_card
from unittest.mock import patch
from unittest import mock
from sktrader.watcher import browser_geometry, _alert_embed, _read_alerts, _read_feed_cards, _collect_replay_history, DIAG_JS, EXTRACT_CARDS_JS, PROBE_CARDS_JS, SCROLL_ALERTS_JS, Config, _post_discord, _bot_token, _one_browser_at_a_time


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


HARMONICS = """HARMONICS
2 minutes ago
BTCUSDT
5M - BUY
Point of Interest
78469.95234
Invalidation
78161.75606
Exit 1
78712.70942
Exit 2
78926.48058
Exit 3
79272.50000"""

WOLFX = """WOLFX
38 minutes ago
XAUUSD
1M - SELL
Point of Interest
4014.53000
Invalidation
4026.55000
Exit 1
4002.51000
Exit 2
3978.47000
Exit 3
3954.43000"""

PRIME = """PRIME
17 minutes ago
XAGUSD
15M - BUY
Point of Interest
51.82150
Zone S0"""


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


class SidebarScopingTests(unittest.TestCase):
    def test_extractors_scope_to_right_side(self):
        # Chart overlay text can also contain Point of Interest labels.
        self.assertIn("window.innerWidth * 0.60", EXTRACT_CARDS_JS)
        self.assertIn("window.innerWidth * 0.60", DIAG_JS)
        self.assertIn("if (!el || !isSidebar(el)) continue;", EXTRACT_CARDS_JS)
        self.assertIn("if (!isSidebar(node.parentElement)) continue;", DIAG_JS)

    def test_chart_annotations_do_not_block_full_sidebar_baseline(self):
        class Page:
            def evaluate(self, script):
                if script == EXTRACT_CARDS_JS:
                    return [BUY, SELL]
                if script == DIAG_JS:
                    return {"document_poi_labels": 10, "poi_labels": 2,
                            "excluded_chart_or_other_labels": 8}
                raise AssertionError("Unexpected script")

        alerts = _read_alerts(Page())
        self.assertEqual(len(alerts), 2)
        self.assertEqual([a.side for a in alerts], ["BUY", "SELL"])


class StructureProbeTests(unittest.TestCase):
    def test_probe_is_bounded_to_sidebar_and_single_card(self):
        self.assertIn("window.innerWidth * 0.60", PROBE_CARDS_JS)
        self.assertIn("out.length < 15", PROBE_CARDS_JS)
        self.assertIn("matches > 1", PROBE_CARDS_JS)
        self.assertIn("slice(0, 650)", PROBE_CARDS_JS)
        self.assertNotIn("outerHTML", PROBE_CARDS_JS)
        self.assertNotIn("document.cookie", PROBE_CARDS_JS)


class IndependentAurouxTimeframeTests(unittest.TestCase):
    @staticmethod
    def _same_prices_three_frames():
        # Screenshot example: three independent Auroux SELL cards have
        # identical prices and scanner levels, but different timeframes.
        base = """AUROUX
55 minutes ago
XAUUSD
30M - SELL
Point of Interest
4024.50000
Invalidation
4034.50000
Exit 1
4014.50000
Exit 2
4004.50000
Level
250 pips"""
        return [
            base,
            base.replace("55 minutes ago", "1 hours ago").replace("30M - SELL", "5M - SELL"),
            base.replace("55 minutes ago", "5 hours ago").replace("30M - SELL", "1H - SELL"),
        ]

    def test_no_timeframe_or_range_filter_is_configured(self):
        with patch.dict(os.environ, {
            "SKTRADER_TIMEFRAME": "5M",
            "SKTRADER_LEVELS": "250,500",
        }):
            config = Config.from_env(require_discord=False)
            self.assertFalse(hasattr(config, "timeframe"))
            self.assertFalse(hasattr(config, "levels"))

    def test_three_equal_price_cards_have_three_distinct_fingerprints(self):
        cards = self._same_prices_three_frames()
        self.assertEqual(len({feed_fingerprint(c) for c in cards}), 3)
        self.assertEqual(
            [parse_card(c).timeframe for c in cards], ["30M", "5M", "1H"],
        )
        self.assertEqual({parse_card(c).poi for c in cards}, {"4024.50000"})
        self.assertEqual({parse_card(c).tp1 for c in cards}, {"4014.50000"})

    def test_all_three_new_cards_are_queued_individually(self):
        cards = self._same_prices_three_frames()
        with tempfile.TemporaryDirectory() as folder:
            store = EventStore(Path(folder) / "events.db")
            old = [HARMONICS, WOLFX, PRIME]
            self.assertEqual(store.observe_feed(old), (0, True))
            self.assertEqual(store.observe_feed(cards + old), (3, False))
            events = store.pending()
            self.assertEqual(len(events), 3)
            self.assertEqual({a.timeframe for _, _, a in events}, {"30M", "5M", "1H"})
            self.assertTrue(all(a.poi == "4024.50000" for _, _, a in events))
            self.assertEqual(store.observe_feed(cards + old), (0, False))
            store.conn.close()

    def test_new_identical_card_is_not_collapsed_when_overlap_exists(self):
        cards = self._same_prices_three_frames()
        with tempfile.TemporaryDirectory() as folder:
            store = EventStore(Path(folder) / "events.db")
            old = cards + [HARMONICS, WOLFX, PRIME]
            self.assertEqual(store.observe_feed(old), (0, True))
            self.assertEqual(store.observe_feed([cards[0]] + old), (1, False))
            self.assertEqual(store.pending()[0][2].timeframe, "30M")
            store.conn.close()


class ScrollableHistoryTests(unittest.TestCase):
    def test_history_appends_older_cards_using_two_card_overlap(self):
        newest = [HARMONICS, WOLFX, PRIME]
        older_page = [WOLFX, PRIME, BUY, SELL]
        self.assertEqual(merge_history_page(newest, older_page),
                         [HARMONICS, WOLFX, PRIME, BUY, SELL])
        self.assertEqual(merge_history_page(newest, newest), newest)
        self.assertEqual(
            merge_history_page(newest, [WOLFX, PRIME]), newest
        )

    def test_history_refuses_ambiguous_one_card_overlap(self):
        with self.assertRaisesRegex(SnapshotError, "two-card overlap"):
            merge_history_page([HARMONICS, WOLFX, PRIME], [PRIME, BUY, SELL])

    def test_scroller_is_limited_to_alerts_sidebar(self):
        self.assertIn("window.innerWidth * 0.60", SCROLL_ALERTS_JS)
        self.assertIn("el.scrollTop", SCROLL_ALERTS_JS)
        self.assertNotIn("window.scrollTo", SCROLL_ALERTS_JS)
        self.assertNotIn("outerHTML", SCROLL_ALERTS_JS)

    def test_history_preserves_latest_feed_cursor(self):
        latest = [HARMONICS, WOLFX, PRIME]
        older = [HARMONICS, WOLFX, PRIME, BUY, SELL]
        with tempfile.TemporaryDirectory() as folder:
            store = EventStore(Path(folder) / "signals.db")
            self.assertEqual(store.replay_visible_once(
                older,
                feed_cursor=latest,
            ), (2, False))
            row = store.conn.execute(
                "SELECT fingerprints FROM feed_snapshot WHERE id=1"
            ).fetchone()
            self.assertEqual(json.loads(row[0]), [feed_fingerprint(c) for c in latest])
            self.assertEqual(store.observe_feed(latest), (0, False))
            store.conn.close()

    def test_collect_history_from_virtualised_scrollable_list(self):
        latest = [HARMONICS, WOLFX, PRIME]
        older = [WOLFX, PRIME, BUY, SELL]
        class Page:
            def __init__(self):
                self.step = 0
            def evaluate(self, script, action=None):
                if script == SCROLL_ALERTS_JS:
                    if action == "top":
                        return {"found": True, "moved": False, "bottom": False}
                    self.step += 1
                    return {"found": True, "moved": self.step == 1,
                            "bottom": self.step >= 2}
                if script == EXTRACT_CARDS_JS:
                    return latest if self.step == 0 else older
                if script == DIAG_JS:
                    return {"poi_labels": 3 if self.step == 0 else 4}
                raise AssertionError("Unexpected page evaluation")
            def wait_for_timeout(self, _ms):
                pass

        archive, cursor, steps, capped = _collect_replay_history(Page(), max_scrolls=4)
        self.assertEqual(archive, latest + [BUY, SELL])
        self.assertEqual(cursor, latest)
        self.assertEqual(steps, 1)
        self.assertFalse(capped)

    def test_replay_scroll_cap_can_be_detected(self):
        class Page:
            def evaluate(self, script, action=None):
                if script == SCROLL_ALERTS_JS:
                    return {"found": True, "moved": True, "bottom": False}
                if script == EXTRACT_CARDS_JS:
                    return [HARMONICS, WOLFX, PRIME]
                if script == DIAG_JS:
                    return {"poi_labels": 3}
                raise AssertionError("Unexpected page evaluation")
            def wait_for_timeout(self, _ms):
                pass

        _, _, steps, capped = _collect_replay_history(Page(), max_scrolls=2)
        self.assertEqual(steps, 2)
        self.assertTrue(capped)


class ReplayVisibleTests(unittest.TestCase):
    def test_one_time_historical_replay_after_existing_baseline(self):
        with tempfile.TemporaryDirectory() as temp:
            filename = Path(temp) / "signals.db"
            store = EventStore(filename)
            old = [HARMONICS, WOLFX, PRIME]
            self.assertEqual(store.observe_feed(old), (0, True))
            cards = [BUY, SELL] + old
            self.assertEqual(store.replay_visible_once(
                cards), (2, False))
            self.assertEqual(len(store.pending()), 2)
            self.assertEqual(
                [item[2].timeframe for item in store.pending()], ["5M", "30M"]
            )
            self.assertTrue(all(store.is_visible_replay_event(x[0]) for x in store.pending()))
            self.assertEqual(store.observe_feed(cards), (0, False))
            store.conn.close()
            reopened = EventStore(filename)
            self.assertEqual(reopened.replay_visible_once(
                cards), (0, True))
            self.assertEqual(len(reopened.pending()), 2)
            for event_id, _, _ in reopened.pending():
                reopened.delivered(event_id)
            self.assertEqual(reopened.pending(), [])
            reopened.conn.close()

    def test_zero_auroux_does_not_use_one_time_replay(self):
        with tempfile.TemporaryDirectory() as temp:
            store = EventStore(Path(temp) / "signals.db")
            old = [HARMONICS, WOLFX, PRIME]
            self.assertEqual(store.replay_visible_once(
                old), (0, False))
            self.assertEqual(store.pending(), [])
            self.assertEqual(store.observe_feed(old), (0, False))
            self.assertEqual(store.replay_visible_once(
                [BUY] + old), (1, False))
            self.assertEqual(len(store.pending()), 1)
            store.conn.close()

    def test_one_time_replay_preserves_individual_same_price_cards(self):
        cards = IndependentAurouxTimeframeTests._same_prices_three_frames()
        with tempfile.TemporaryDirectory() as temp:
            store = EventStore(Path(temp) / "signals.db")
            self.assertEqual(store.replay_visible_once(
                cards + [HARMONICS]), (3, False))
            self.assertEqual({a.timeframe for _, _, a in store.pending()},
                             {"30M", "5M", "1H"})
            store.conn.close()

    def test_manual_replay_sends_every_supported_timeframe(self):
        with tempfile.TemporaryDirectory() as temp:
            store = EventStore(Path(temp) / "signals.db")
            cards = IndependentAurouxTimeframeTests._same_prices_three_frames()
            self.assertEqual(store.replay_visible_once(cards), (3, False))
            self.assertEqual({alert.timeframe for _, _, alert in store.pending()},
                             {"30M", "5M", "1H"})
            store.conn.close()

    def test_bad_target_card_cannot_create_replay_batch(self):
        with tempfile.TemporaryDirectory() as temp:
            store = EventStore(Path(temp) / "signals.db")
            broken = BUY.replace("4015.50000", "4030.50000")
            with self.assertRaisesRegex(SnapshotError, "Malformed historical"):
                store.replay_visible_once(
                    [broken, HARMONICS])
            self.assertEqual(store.pending(), [])
            self.assertEqual(store.replay_visible_once(
                [BUY, HARMONICS]), (1, False))
            store.conn.close()

    def test_historical_replay_uses_the_same_card_only_format(self):
        alert = parse_card(BUY)
        historical = _alert_embed(19, "2026-10-29T12:00:00Z", alert, historical=True)
        realtime = _alert_embed(20, "2026-10-30T12:00:00Z", alert)
        self.assertEqual(historical, realtime)
        self.assertEqual(historical["embeds"][0]["title"], "AUROUX")
        self.assertNotIn("footer", historical["embeds"][0])

    def test_browser_profile_single_instance_lock(self):
        with tempfile.TemporaryDirectory() as temp:
            profile = Path(temp) / "profile"
            with _one_browser_at_a_time(profile):
                with self.assertRaisesRegex(RuntimeError, "Another Sktrader browser"):
                    with _one_browser_at_a_time(profile):
                        pass
            with _one_browser_at_a_time(profile):
                pass


class AurouxAllRangesTests(unittest.TestCase):
    def test_replay_includes_50_and_100_as_well_as_250_and_500(self):
        with tempfile.TemporaryDirectory() as temp:
            store = EventStore(Path(temp) / "signals.db")
            cards = [BUY.replace("250 pips", str(level) + " pips")
                     for level in (50, 100, 250, 500)]
            self.assertEqual(store.replay_visible_once(cards), (4, False))
            self.assertEqual({a.level for _, _, a in store.pending()}, {50, 100, 250, 500})
            store.conn.close()


class MixedFeedTests(unittest.TestCase):
    def test_non_auroux_scanners_are_not_eligible(self):
        self.assertFalse(auroux_xauusd_card(WOLFX))
        self.assertFalse(auroux_xauusd_card(HARMONICS))
        self.assertFalse(auroux_xauusd_card(PRIME))
        self.assertTrue(auroux_xauusd_card(BUY))
        self.assertFalse(auroux_xauusd_card(BUY.replace("XAUUSD", "EURUSD")))

    def test_relative_age_does_not_change_feed_identity(self):
        self.assertEqual(feed_fingerprint(HARMONICS),
                         feed_fingerprint(HARMONICS.replace("2 minutes ago", "1 hour ago")))
        self.assertEqual(feed_fingerprint(WOLFX),
                         feed_fingerprint(WOLFX.replace("38 minutes ago", "39 minutes ago")))

    def test_mixed_feed_read_is_complete_and_filters_auroux(self):
        cards = [HARMONICS, WOLFX, PRIME, BUY]
        class Page:
            def evaluate(self, script):
                if script == EXTRACT_CARDS_JS:
                    return cards
                if script == DIAG_JS:
                    return {"poi_labels": 4, "has_auroux": True}
                raise AssertionError("Unexpected script")

        self.assertEqual(len(_read_feed_cards(Page())), 4)
        alerts = _read_alerts(Page())
        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0].scanner, "AUROUX")

    def test_mixed_feed_without_any_auroux_is_valid(self):
        cards = [HARMONICS, WOLFX, PRIME]
        class Page:
            def evaluate(self, script):
                if script == EXTRACT_CARDS_JS:
                    return cards
                if script == DIAG_JS:
                    return {"poi_labels": 3}
                raise AssertionError("Unexpected script")

        self.assertEqual(_read_alerts(Page()), [])

    def test_mixed_feed_sequence_detects_new_auroux_without_replaying_history(self):
        with tempfile.TemporaryDirectory() as temp:
            db = EventStore(Path(temp) / "events.db")
            old = [HARMONICS, WOLFX, PRIME]
            self.assertEqual(db.observe_feed(old), (0, True))
            self.assertEqual(db.pending(), [])
            # New Auroux post appears above three other scanners.
            self.assertEqual(db.observe_feed([BUY] + old), (1, False))
            pending = db.pending()
            self.assertEqual(len(pending), 1)
            self.assertEqual(pending[0][2].symbol, "XAUUSD")
            self.assertEqual(pending[0][2].scanner, "AUROUX")
            db.delivered(pending[0][0])
            self.assertEqual(db.pending(), [])
            # Relative age advances on every rendered card.
            older = [BUY.replace("6 minutes ago", "7 minutes ago"),
                     HARMONICS.replace("2 minutes ago", "3 minutes ago"),
                     WOLFX.replace("38 minutes ago", "39 minutes ago"),
                     PRIME.replace("17 minutes ago", "18 minutes ago")]
            self.assertEqual(db.observe_feed(older), (0, False))
            # Other providers and instruments do not generate delivery.
            self.assertEqual(db.observe_feed([HARMONICS] + older), (0, False))
            self.assertEqual(db.pending(), [])
            db.conn.close()

    def test_malformed_new_auroux_fails_before_snapshot_advance(self):
        with tempfile.TemporaryDirectory() as temp:
            db = EventStore(Path(temp) / "events.db")
            old = [HARMONICS, WOLFX, PRIME]
            self.assertEqual(db.observe_feed(old), (0, True))
            malformed = BUY.replace("4015.50000", "4030.50000")
            with self.assertRaisesRegex(SnapshotError, "Unparseable new AUROUX"):
                db.observe_feed([malformed] + old)
            self.assertEqual(db.observe_feed([BUY] + old), (1, False))
            db.conn.close()


class BrowserDiagnosticsTests(unittest.TestCase):
    def test_empty_cards_report_safe_diagnostic(self):
        class Page:
            def evaluate(self, script):
                if script == EXTRACT_CARDS_JS:
                    return []
                if script == DIAG_JS:
                    return {"poi_labels": 3, "possible_login": False, "has_auroux": True}
                self.fail("Unexpected script")

        with self.assertRaises(SnapshotError) as caught:
            _read_alerts(Page())
        self.assertIn('"poi_labels": 3', str(caught.exception))
        self.assertNotIn("password", str(caught.exception).lower())

    def test_valid_card_is_parsed(self):
        class Page:
            def evaluate(self, script):
                if script == EXTRACT_CARDS_JS:
                    return [BUY]
                if script == DIAG_JS:
                    return {"poi_labels": 1}
                raise AssertionError("Unexpected script")

        self.assertEqual(_read_alerts(Page())[0].poi, "4025.50000")


    def test_incomplete_list_refuses_baseline(self):
        class Page:
            def evaluate(self, script):
                if script == EXTRACT_CARDS_JS:
                    return [BUY]
                if script == DIAG_JS:
                    return {"poi_labels": 10}
                raise AssertionError("Unexpected script")

        with self.assertRaisesRegex(SnapshotError, "1 cards versus 10 visible labels"):
            _read_alerts(Page())


class ExistingDiscordBotTests(unittest.TestCase):
    def test_delivery_uses_bot_identity_and_exact_channel(self):
        with tempfile.TemporaryDirectory() as folder:
            secret = Path(folder) / "bot-token"
            secret.write_text("test-DKCapital-bot-secret-123456789", encoding="utf-8")
            secret.chmod(0o600)
            with patch.dict(os.environ, {
                "SKTRADER_DISCORD_CHANNEL_ID": "1565508564775075981",
                "SKTRADER_DISCORD_BOT_TOKEN_FILE": str(secret),
            }):
                config = Config.from_env(require_discord=True)
            with patch("sktrader.watcher.urllib.request.urlopen") as opened:
                opened.return_value.__enter__.return_value.status = 200
                _post_discord(config, {"content": "test", "allowed_mentions": {"parse": []}}, event_id=5)
                request = opened.call_args.args[0]
            self.assertEqual(request.full_url, "https://discord.com/api/v10/channels/1565508564775075981/messages")
            self.assertEqual(request.get_header("Authorization"), "Bot test-DKCapital-bot-secret-123456789")
            self.assertEqual(request.get_method(), "POST")
            self.assertEqual(json.loads(request.data), {
                "content": "test", "allowed_mentions": {"parse": []},
                "nonce": "sktrader-5", "enforce_nonce": True,
            })

    def test_rejects_token_file_with_group_permissions(self):
        with tempfile.TemporaryDirectory() as folder:
            secret = Path(folder) / "bot-token"
            secret.write_text("test-DKCapital-bot-secret-123456789", encoding="utf-8")
            secret.chmod(0o640)
            with self.assertRaises(PermissionError):
                _bot_token(secret)

    def test_channel_id_required_for_watcher(self):
        with patch.dict(os.environ, {
            "SKTRADER_DISCORD_CHANNEL_ID": "",
            "SKTRADER_DISCORD_BOT_TOKEN_FILE": "/tmp/some-private-file",
        }):
            with self.assertRaisesRegex(ValueError, "channel snowflake"):
                Config.from_env(require_discord=True)


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

    def test_discord_embed_matches_source_card_and_no_extras(self):
        x = _alert_embed(3, "2026-10-29T12:00:00Z", parse_card(SELL))
        card = x["embeds"][0]
        self.assertEqual(card["title"], "AUROUX")
        self.assertIn("XAUUSD", card["description"])
        self.assertIn("5M - SELL", card["description"])
        self.assertEqual([f["name"] for f in card["fields"]],
                         ["Point of Interest", "Invalidation", "Exit 1", "Exit 2", "Level"])
        self.assertNotIn("footer", card)
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
