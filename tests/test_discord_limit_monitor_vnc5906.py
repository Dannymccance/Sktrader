"""Guard the Discord-only monitor listener: never send broker orders."""
from pathlib import Path
import subprocess
import unittest

ROOT=Path(__file__).resolve().parents[1]
SCRIPT=ROOT/"deploy"/"setup-discord-limit-vnc5906.sh"

class DiscordMonitor5906Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source=SCRIPT.read_text()

    def test_shell_syntax(self):
        subprocess.run(["bash","-n",str(SCRIPT)],check=True,capture_output=True)

    def test_scoped_files(self):
        s=self.source
        for v in (
            "user=dkvnc",
            "home=/var/lib/dkcapital-desktop",
            "-rfbport 5906 -localhost yes",
            "channel=1565527578163552327",
            "sktrader.discord_limit_bridge",
            "discord-limit-monitor.csv",
            "discord-limit-monitor.db",
            "NoNewPrivileges=true",
            "UMask=0077",
            "bot_profile(token,sys.argv[2])",
        ):
            self.assertIn(v,s)

    def test_bridge_uses_dedicated_venv(self):
        s=self.source
        self.assertIn('venv="$home/.venv-discord-limit"',s)
        self.assertIn('python="$venv/bin/python"',s)
        self.assertIn('python3 -m venv "$venv"',s)
        self.assertIn("'websockets>=14,<17'",s)
        self.assertIn('"$python" -m pip install',s)
        self.assertIn("PYTHONPATH=/opt/sktrader",s)
        self.assertIn(
            "ExecStart=/var/lib/dkcapital-desktop/.venv-discord-limit/bin/python -u -m sktrader.discord_limit_bridge",
            s,
        )
        self.assertNotIn("ExecStart=/opt/sktrader/.venv/bin/python",s)
        self.assertIn('[[ -e "$venv" || -L "$venv" ]]',s)
        self.assertLess(s.index('if [[ -e "$unit" || -L "$unit" ]]; then'),
                        s.index('python3 -m venv "$venv"'))

    def test_monitor_cannot_write_actionable_mt5_csv(self):
        s=self.source
        self.assertIn('output="$private/discord-limit-monitor.csv"',s)
        self.assertIn('db="$private/discord-limit-monitor.db"',s)
        self.assertIn('[[ -e "$mt5common/discord_limit_30m_signals.csv" ]]',s)
        self.assertIn("Output is isolated:",s)
        self.assertIn("trading NOT connected",s)
        self.assertIn("MT5 Common/Files is deliberately UNTOUCHED",s)
        self.assertNotIn("--output /var/lib/dkcapital-desktop/.wine-mt5",s)
        for forbidden in (
            "systemctl restart dkcapital",
            "systemctl stop dkcapital",
            "sktrader.service --now",
            "OrderSend",
            "EnableNewEntries=true",
            "pkill",
            "rm -rf",
        ):
            self.assertNotIn(forbidden,s)

if __name__=="__main__":
    unittest.main()
