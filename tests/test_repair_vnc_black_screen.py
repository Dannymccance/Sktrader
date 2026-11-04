"""Targeted repair for black VNC desktop must not affect trading services."""
import subprocess
import unittest
from pathlib import Path

SCRIPT=Path(__file__).resolve().parents[1]/"deploy"/"repair-vnc-black-screen.sh"


class VNCScreenRepairTests(unittest.TestCase):
    def test_shell_syntax(self):
        subprocess.run(["bash","-n",str(SCRIPT)],check=True,capture_output=True)

    def test_repair_only_5905_service(self):
        s=SCRIPT.read_text()
        self.assertIn("dkcapital-desktop-vnc.service",s)
        self.assertIn("User=dkvnc",s)
        self.assertIn("-rfbport 5905 -localhost yes",s)
        self.assertIn('systemctl restart "$unit"',s)
        self.assertIn("Only the new :5 / 5905 desktop service was restarted",s)
        for forbidden in ("systemctl restart dkcapital-mt5","systemctl stop dkcapital-mt5",
                          "systemctl stop sktrader","docker stop","docker rm",
                          "rm -rf /var/lib","systemctl restart dkcapital-vnc"):
            self.assertNotIn(forbidden,s)

    def test_visible_fallback_and_clipboard(self):
        s=SCRIPT.read_text()
        self.assertIn("xfce4-session",s)
        self.assertIn("dbus-run-session",s)
        self.assertNotIn("startxfce4",s.split("cat > \"$startup\" <<'STARTUP'")[1].split("STARTUP")[0])
        self.assertIn("/usr/bin/xterm",s)
        self.assertIn("tigervncconfig -nowin",s)
        self.assertIn("autocutsel -fork -selection CLIPBOARD",s)
        self.assertIn("autocutsel -fork -selection PRIMARY",s)
        self.assertIn("xstartup.backup.",s)
        self.assertIn("xfce-session.log",s)


if __name__=="__main__":
    unittest.main()
