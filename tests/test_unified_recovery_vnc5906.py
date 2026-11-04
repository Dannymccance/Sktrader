"""Safety and deterministic behavior of isolated trading recovery."""
import importlib.util
import os
from pathlib import Path
import subprocess
import tempfile
import time
import unittest
from unittest import mock

from sktrader.discord_limit_bridge import GatewayHealth, SignalStore, LIFETIME_SECONDS

ROOT=Path(__file__).resolve().parents[1]
SCRIPT=ROOT/"deploy"/"unified_recovery_vnc5906.py"
INSTALLER=ROOT/"deploy"/"install-unified-recovery-vnc5906.sh"
spec=importlib.util.spec_from_file_location("unified_recovery_vnc5906",SCRIPT)
recovery=importlib.util.module_from_spec(spec)
spec.loader.exec_module(recovery)


class RecoveryWatchdogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.installer=INSTALLER.read_text()
        cls.recovery=SCRIPT.read_text()

    def test_installer_bash_syntax(self):
        subprocess.run(["bash","-n",str(INSTALLER)],check=True,capture_output=True)

    def test_gateway_health_written_without_credentials(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/"health.json"
            h=GatewayHealth(p)
            h.write("connecting",interval_s=42.0)
            h.write("ready",ack=True)
            import json
            data=json.loads(p.read_text())
            self.assertTrue(recovery.ready_gateway(data,time.time(),expected_pid=os.getpid()))
            self.assertFalse(recovery.ready_gateway(data,time.time(),expected_pid=os.getpid()+1))
            self.assertFalse(recovery.ready_gateway({**data,"last_ack_at":time.time()-500},
                                                     time.time(),expected_pid=os.getpid()))
            self.assertEqual(p.stat().st_mode & 0o077,0)
            self.assertNotIn("token",p.read_text().lower())
            h.write("disconnected")
            self.assertFalse(recovery.ready_gateway(json.loads(p.read_text()),time.time()))

    def test_fresh_instruction_only_after_recovery(self):
        with tempfile.TemporaryDirectory() as d:
            now=int(time.time())
            db=Path(d)/"live.db"
            csvfile=Path(d)/"feed.csv"
            store=SignalStore(db,csvfile,max_input_age_sec=60)
            old=("1567669630498119835","SELL","4167.53",now-120,now-120+LIFETIME_SECONDS)
            recent=("1567674184375603342","SELL","4167.53",now-5,now-5+LIFETIME_SECONDS)
            self.assertFalse(store.add(old,now_ms=now*1000))
            self.assertTrue(store.add(recent,now_ms=now*1000))
            self.assertIn(recent[0],csvfile.read_text())
            store.publish(now_s=now+120)
            self.assertEqual(csvfile.read_text(),"")
            store.con.close()
            # Simulate a service restart 120s later, not at test wall-clock now.
            with mock.patch("sktrader.discord_limit_bridge.time.time", return_value=now+120):
                restarted=SignalStore(db,csvfile,max_input_age_sec=60)
            self.assertEqual(csvfile.read_text(),"")
            restarted.con.close()

    def test_legacy_thirty_minute_policy_unmodified(self):
        with tempfile.TemporaryDirectory() as d:
            now=int(time.time())
            store=SignalStore(Path(d)/"legacy.db",Path(d)/"signal.csv")
            old=("1567669630498119835","SELL","4167.53",now-120,now-120+LIFETIME_SECONDS)
            self.assertTrue(store.add(old,now_ms=now*1000))
            self.assertIn(old[0],(Path(d)/"signal.csv").read_text())
            store.con.close()

    def test_restart_backoff_capped(self):
        state={"components":{}}
        now=100000
        self.assertTrue(recovery.bounded_attempt(state,"gateway",now))
        self.assertFalse(recovery.bounded_attempt(state,"gateway",now+2))
        self.assertTrue(recovery.bounded_attempt(state,"gateway",now+241))
        self.assertTrue(recovery.bounded_attempt(state,"gateway",now+482))
        self.assertFalse(recovery.bounded_attempt(state,"gateway",now+724))
        self.assertTrue(recovery.bounded_attempt(state,"gateway",now+3601))

    def test_never_kill_running_mt5_and_no_legacy_restart(self):
        s=self.recovery
        for required in (
            "if present and mt5_fail >= 2:",
            "refusing to kill/restart a potentially trading terminal",
            "elif not present and mt5_fail >= 2 and display_ok and gateway_ok:",
            "feed_fresh",
            "systemd-run",
            "--property=User=dkvnc",
            "--setenv=DISPLAY=:5",
            "checked_unit()",
            "Restarting only the authorized live Discord listener",
            "Restarting isolated VNC 5906 with no MT5 process present",
            "JIFU collector service",
            "admin_channel()",
        ):
            self.assertIn(required,s)
        for forbidden in ("pkill","killall","wineboot","systemctl stop",
                          'run("systemctl", "restart", JIFU',
                          "OrderSend","trade.Buy","trade.Sell","rm -rf"):
            self.assertNotIn(forbidden,s)

    def test_installation_changes_only_live_bridge(self):
        s=self.installer
        for required in ("--health-file $health --max-input-age-sec 60",
                         "--not-before-ms",
                         "--author-id 1554225452091048017 --author-id 508740193369587722",
                         "discord-limit-live.db","systemctl restart \"$live\"",
                         "systemctl enable --now \"$recovery.timer\""):
            self.assertIn(required,s)
        self.assertNotIn("systemctl restart sktrader.service",s)
        self.assertNotIn("systemctl restart dkcapital-mt5",s)
        self.assertNotIn("systemctl restart dkcapital-desktop-vnc",s)


if __name__=="__main__":
    unittest.main()
