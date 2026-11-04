"""Protect live MT5 accounts when preparing a new isolated VNC 5906 installer."""
import subprocess
import unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
SCRIPT=ROOT/"deploy"/"install-mt5-vnc5906.sh"


class MT5NewVNCInstallTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source=SCRIPT.read_text()

    def test_shell_syntax(self):
        subprocess.run(["bash","-n",str(SCRIPT)],capture_output=True,check=True)

    def test_single_new_vnc_user_and_prefix(self):
        src=self.source
        self.assertIn("user=dkvnc",src)
        self.assertIn("home=/var/lib/dkcapital-desktop",src)
        self.assertIn('prefix="$home/.wine-mt5"',src)
        self.assertIn("-rfbport 5906 -localhost yes",src)
        self.assertIn("compiler=dkcapital-mt5-compiler-vnc",src)
        self.assertIn("Run as dkvnc in VNC desktop",src)
        self.assertIn("export WINEARCH=win64",src)

    def test_source_and_gui_launchers(self):
        src=self.source
        self.assertIn("https://download.mql5.com/cdn/web/metaquotes.software.corp/mt5/mt5setup.exe",src)
        self.assertIn('f.open("rb").read(2)!=b"MZ"',src)
        self.assertIn("Install MetaTrader 5.desktop",src)
        self.assertIn("MetaTrader 5.desktop",src)
        self.assertIn("exec wine",src)

    def test_no_broker_or_service_changes(self):
        src=self.source
        for forbidden in (
            "systemctl restart", "systemctl stop", "systemctl enable",
            "docker stop", "docker rm",
            "rm -rf", "AccountLogin", "MT5_PASSWORD",
            "dkcapital-mt5-3/.wine", "sktrader-auroux-bridge.service",
        ):
            self.assertNotIn(forbidden,src)


if __name__=="__main__":
    unittest.main()
