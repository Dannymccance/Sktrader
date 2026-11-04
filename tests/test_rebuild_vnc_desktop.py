"""Guard the VNC cleanup from accidental MT5/collector or compiler disruption."""
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "deploy" / "rebuild-vnc-desktop.sh"


class RebuildVNCSafetyTests(unittest.TestCase):
    def test_bash_syntax(self):
        subprocess.run(["bash", "-n", str(SCRIPT)], check=True, capture_output=True)

    def test_old_services_are_observation_only(self):
        s = SCRIPT.read_text()
        for service in ("dkcapital-vnc1", "dkcapital-vnc2", "dkcapital-vnc3"):
            self.assertIn(service, s)
        self.assertIn('grep -Fq -- "-rfbport $port -localhost"', s)
        self.assertIn('grep -Fq -- "-display $display"', s)
        self.assertIn('mv "$path" "$backup/$unit.service"', s)
        self.assertNotIn("rm -rf /var/lib/dkcapital", s)
        self.assertNotIn("systemctl stop dkcapital-mt5", s)
        self.assertNotIn("systemctl restart dkcapital-mt5", s)
        self.assertNotIn("docker stop", s)
        self.assertNotIn("docker rm", s)

    def test_compiler_and_jifu_are_preserved(self):
        s = SCRIPT.read_text()
        self.assertIn("COMPILER=dkcapital-mt5-compiler-vnc", s)
        self.assertIn("sktrader-display", s)
        self.assertIn('docker inspect -f', s)
        self.assertIn("Kept compiler 5903", s)

    def test_new_desktop_clipboard_and_private_binding(self):
        s = SCRIPT.read_text()
        self.assertIn("tigervnc-standalone-server", s)
        self.assertIn("autocutsel", s)
        self.assertIn("tigervncconfig -nowin", s)
        self.assertIn("xclip -selection clipboard", s)
        self.assertIn("DISPLAY=:5", s)
        self.assertIn("-localhost yes", s)
        self.assertIn("-rfbport 5906", s)
        self.assertIn("127", s)
        self.assertIn('read -r -s -p "VNC password: "', s)
        self.assertIn("tigervncpasswd -f", s)
        self.assertIn("New clipboard self-test failed; legacy services remain unchanged", s)

    def test_retirement_after_new_desktop_verified(self):
        s = SCRIPT.read_text()
        check = 'echo "NEW desktop :5, port 5906 and X11 clipboard self-test PASSED."'
        stop = 'systemctl disable --now "$unit.service"'
        self.assertLess(s.index(check), s.index(stop))


if __name__ == "__main__":
    unittest.main()
