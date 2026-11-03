"""Safety checks for the one-command demo-only AUROUX deployment.

GitHub Python CI cannot compile MQL5 or access a broker. These checks
validate the deployment shell and its non-negotiable safeguards.
"""
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "deploy" / "repair-auroux-demo.sh"
EA = ROOT / "mt5" / "AurouxFollower.mq5"


class AurouxDemoDeploymentTests(unittest.TestCase):
    def test_shell_syntax_valid(self):
        subprocess.run(["bash", "-n", str(SCRIPT)], check=True, capture_output=True)

    def test_deployment_does_not_touch_live_mt5_or_gws_services(self):
        source = SCRIPT.read_text()
        self.assertIn("sktrader-auroux-bridge.service", source)
        self.assertIn('systemctl restart "$service"', source)
        self.assertNotIn("systemctl restart dkcapital-mt5", source)
        self.assertNotIn("systemctl restart gws", source)
        self.assertIn("broker account telemetry is stale", source)
        self.assertIn('"mode":"DEMO"', source)
        self.assertIn('"login":"5058169035"', source)
        self.assertIn('[[ "$ready" == 1 ]]', source)
        self.assertIn('"trade_allowed"]=="1"', source)

    def test_ea_fails_closed_on_wrong_account_and_heartbeats(self):
        ea = EA.read_text()
        self.assertIn('input long ExpectedDemoLogin = 5058169035;', ea)
        self.assertIn("AccountInfoInteger(ACCOUNT_TRADE_MODE)!=ACCOUNT_TRADE_MODE_DEMO", ea)
        self.assertIn("AccountInfoInteger(ACCOUNT_LOGIN)!=ExpectedDemoLogin", ea)
        self.assertIn("return INIT_FAILED;", ea)
        self.assertIn('FileOpen("auroux_follower_health.csv"', ea)
        self.assertIn('void OnTimer() {\n   WriteHealth();', ea)
        self.assertIn('input bool RoundVolumeUp = false;', ea)


if __name__ == "__main__":
    unittest.main()
