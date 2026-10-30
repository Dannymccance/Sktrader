import json
import os
import sys
import types
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

from sktrader.recovery import HealthMonitor, login_state, private_credentials, try_login


class RecoveryTests(unittest.TestCase):
    def test_login_detection_does_not_guess_from_empty_page(self):
        self.assertEqual(login_state({"possible_login": True, "has_alerts_tab": False}), "login")
        self.assertEqual(login_state({"possible_login": False, "has_alerts_tab": True}), "dashboard")
        self.assertEqual(login_state({"body_characters": 260}), "unknown")
        self.assertEqual(login_state({"possible_login": True, "has_alerts_tab": True}), "dashboard")

    def test_credentials_require_secure_user_owned_file(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "jifu-private.json"
            path.write_text(json.dumps({"email": "example@example.com", "password": "testvalue"}))
            path.chmod(0o600)
            self.assertEqual(private_credentials(path)["email"], "example@example.com")
            path.chmod(0o644)
            with self.assertRaises(PermissionError):
                private_credentials(path)
            path.chmod(0o600)
            path.write_text("{}")
            with self.assertRaises(ValueError):
                private_credentials(path)

    def test_health_suppresses_until_two_minutes_and_throttles(self):
        config = object()
        with patch.dict(os.environ, {"SKTRADER_HEALTH_CHANNEL_ID": "0"}):
            monitor = HealthMonitor(config)
        with patch("sktrader.recovery.time.monotonic", side_effect=[100., 180., 221., 240., 3900.]):
            with patch.object(monitor, "_send") as send:
                monitor.degraded("empty")
                monitor.degraded("empty")
                send.assert_not_called()
                monitor.degraded("empty")
                send.assert_called_once()
                monitor.healthy()
                self.assertEqual(send.call_count, 2)

    def test_username_login_in_same_form(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "login.json"
            path.write_text(json.dumps({"email": "danny1888", "password": "secret"}))
            path.chmod(0o600)
            page = MagicMock()
            password = page.locator.return_value
            password.count.return_value = 1
            form = password.locator.return_value
            form.count.return_value = 1
            username = form.locator.return_value
            username.count.return_value = 1
            button = form.locator.return_value
            button.count.return_value = 1
            with patch.dict(sys.modules, {"playwright.sync_api": types.SimpleNamespace(TimeoutError=TimeoutError)}):
                self.assertTrue(try_login(page, path))
            username.fill.assert_called_once_with("danny1888")
            password.fill.assert_called_once_with("secret")
            button.click.assert_called_once()

    def test_multiple_submit_controls_fail_closed_before_credentials(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "login.json"
            path.write_text(json.dumps({"email": "danny1888", "password": "secret"}))
            path.chmod(0o600)
            page = MagicMock()
            password = page.locator.return_value
            password.count.return_value = 1
            form = password.locator.return_value
            form.count.return_value = 1
            user = form.locator.return_value
            user.count.return_value = 1
            form.locator.return_value.count.side_effect = [1, 2]
            with patch.dict(sys.modules, {"playwright.sync_api": types.SimpleNamespace(TimeoutError=TimeoutError)}):
                with self.assertRaisesRegex(RuntimeError, "Ambiguous JIFU submit"):
                    try_login(page, path)
            password.fill.assert_not_called()
            user.fill.assert_not_called()

    def test_ambiguous_login_form_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "login.json"
            path.write_text(json.dumps({"email": "danny1888", "password": "secret"}))
            path.chmod(0o600)
            page = MagicMock()
            page.locator.return_value.count.return_value = 2
            with self.assertRaisesRegex(RuntimeError, "Ambiguous"):
                with patch.dict(sys.modules, {"playwright.sync_api": types.SimpleNamespace(TimeoutError=TimeoutError)}):
                    try_login(page, path)
            page.locator.return_value.fill.assert_not_called()

    def test_health_delivery_failure_does_not_crash_watcher(self):
        with patch.dict(os.environ, {"SKTRADER_HEALTH_CHANNEL_ID": "1565527732941754419"}):
            monitor = HealthMonitor(MagicMock())
        with patch("sktrader.watcher._bot_token", side_effect=PermissionError("private")):
            monitor._send("monitoring interrupted")

    def test_health_default_has_no_signal_destination(self):
        with patch.dict(os.environ, {"SKTRADER_HEALTH_CHANNEL_ID": ""}):
            self.assertEqual(HealthMonitor(object()).channel, 0)


if __name__ == "__main__":
    unittest.main()
