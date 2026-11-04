"""Stage ONLY the verified unified EX5 in the isolated 5906 MT5 desktop."""
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "deploy" / "stage-unified-ea-vnc5906.sh"


class StageUnifiedEA5906Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = SCRIPT.read_text()

    def test_bash_syntax(self):
        subprocess.run(["bash", "-n", str(SCRIPT)], capture_output=True, check=True)

    def test_verified_compile_artifact(self):
        src = self.source
        self.assertIn("DiscordLimitUnified_d4385f2a89df.ex5", src)
        self.assertIn("1905a6017590ba8b19a0975bee0ebfbf2ed5044e0879e5cf1cf760b58d38c8c3", src)
        self.assertIn("1295fa33add8fbe406500373133781ae0c7f2be12908327985a8e7adefa3ac95", src)
        self.assertIn('actual_source=$(sha256sum "$source"', src)
        self.assertIn('actual_artifact=$(sha256sum "$artifact"', src)

    def test_safe_directory_resolution(self):
        src = self.source
        self.assertIn("home=/var/lib/dkcapital-desktop", src)
        self.assertIn("user=dkvnc", src)
        self.assertIn("-rfbport 5906 -localhost yes", src)
        self.assertIn('roaming="$prefix/drive_c/users/$user/AppData/Roaming/MetaQuotes/Terminal"', src)
        self.assertIn('if (( ${#experts_dirs[@]} != 1 )); then', src)
        # This live VNC terminal stores Experts under Program Files with
        # Bases, Config and logs, not in the standard Roaming folder.
        self.assertIn('portable_experts="$program/MQL5/Experts"', src)
        self.assertIn('experts_dirs+=("$portable_experts")', src)
        self.assertIn('[[ -d "$roaming" ]]', src)
        self.assertIn('[[ -d "$program/Config" &&', src)
        self.assertIn('-d "$program/Bases" && -d "$program/logs"', src)
        # If both layouts exist, refuse to pick one at random.
        self.assertIn('if (( ${#experts_dirs[@]} != 1 )); then', src)
        self.assertIn('data directory ambiguous/missing', src)
        self.assertIn("realpath -e", src)
        self.assertIn("outside the isolated MT5 Wine prefix", src)

    def test_no_overwrite_or_activate(self):
        src = self.source
        self.assertIn('cmp -s "$artifact" "$target"', src)
        self.assertIn('ln -- "$tmp" "$target"', src)
        self.assertIn("EnableNewEntries=false", src)
        for forbidden in (
            "systemctl restart",
            "systemctl stop",
            "systemctl enable",
            "docker stop",
            "docker restart",
            "OrderSend(",
            "rm -rf",
            "pkill",
            "dkcapital-mt5-3/.wine",
        ):
            self.assertNotIn(forbidden, src)


if __name__ == "__main__":
    unittest.main()
