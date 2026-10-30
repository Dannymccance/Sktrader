import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sktrader.recovery import HealthMonitor, login_state, private_credentials


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

    def test_health_default_has_no_signal_destination(self):
        with patch.dict(os.environ, {"SKTRADER_HEALTH_CHANNEL_ID": ""}):
            self.assertEqual(HealthMonitor(object()).channel, 0)


if __name__ == "__main__":
    unittest.main()
