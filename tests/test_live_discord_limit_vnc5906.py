"""Guarantee live Discord feed routes to only the intended MT5 desktop."""
import subprocess
import unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
SCRIPT=ROOT/"deploy"/"live-discord-limit-vnc5906.sh"
BRIDGE=ROOT/"sktrader"/"discord_limit_bridge.py"

class LiveDiscordFeedTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source=SCRIPT.read_text()
        cls.bridge=BRIDGE.read_text()

    def test_shell_syntax(self):
        subprocess.run(["bash","-n",str(SCRIPT)],check=True,capture_output=True)

    def test_isolated_accounts_source_and_destination(self):
        s=self.source
        for text in ("user=dkvnc", "home=/var/lib/dkcapital-desktop",
                     "discord_limit_30m_signals.csv",
                     "discord_limit_account.csv",
                     "sktrader-discord-limit-live.service",
                     "sktrader-discord-limit-monitor.service",
                     "sktrader-discord-limit-30m.service",
                     "bot_author=1554225452091048017",
                     "human_author=508740193369587722",
                     "--author-id 1554225452091048017 --author-id 508740193369587722",
                     "--not-before-ms $cutoff_ms",
                     "MemoryMax=400M", "NoNewPrivileges=true", "UMask=0077"):
            self.assertIn(text,s)

    def test_no_replay_and_no_duplicate_active_listeners(self):
        s=self.source
        for text in ("discord-limit-live.db",
                     "cutoff_ms=$(date -u +%s%3N)",
                     "Live DB exists. Refusing to reinitialize",
                     "Signal file already exists. Refusing to clobber",
                     "Legacy actionable Discord feed active",
                     "Live signal database already exists",
                     "legacy=sktrader-discord-limit-30m.service"):
            self.assertIn(text,s)
        self.assertIn("if created < not_before_ms:",self.bridge)
        self.assertIn('action="${1:-inspect}"',s)

    def test_no_alteration_of_other_trading_systems(self):
        s=self.source
        self.assertIn("The EA controls actual broker orders",s)
        self.assertIn("Keep EA EnableNewEntries=false",s)
        for forbidden in ("systemctl restart sktrader.service",
                          "systemctl stop sktrader.service",
                          "systemctl restart dkcapital-mt5",
                          "systemctl stop dkcapital-mt5",
                          "pkill", "rm -rf",
                          "wineboot", "OrderSend",
                          "EnableNewEntries=true"):
            self.assertNotIn(forbidden,s)

if __name__=="__main__":
    unittest.main()