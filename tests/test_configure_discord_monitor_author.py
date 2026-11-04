"""Ensure personal Discord author selection never connects a live MT5 trading feed."""
from pathlib import Path
import subprocess
import unittest

ROOT=Path(__file__).resolve().parents[1]
SCRIPT=ROOT/"deploy"/"configure-discord-monitor-author.sh"

class AuthorSelectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.s=SCRIPT.read_text()

    def test_bash_syntax(self):
        subprocess.run(["bash","-n",str(SCRIPT)],check=True,capture_output=True)

    def test_inspect_recent_authors_without_message_contents(self):
        s=self.s
        self.assertIn("/channels/{channel}/messages?limit=15",s)
        self.assertIn("author_id=",s)
        self.assertIn("name=",s)
        self.assertIn("Discord read-message-history request failed:",s)
        self.assertIn("message contents were not printed",s)
        self.assertIn("Read Message History",s)

    def test_set_only_monitor_author_with_privileged_intent_warning(self):
        s=self.s
        self.assertIn("monitor_service=sktrader-discord-limit-monitor.service",s)
        self.assertIn("ExecStart=/var/lib/dkcapital-desktop/.venv-discord-limit/bin/python",s)
        self.assertIn("--author-id $author",s)
        self.assertIn("MESSAGE_CONTENT privileged intent",s)
        self.assertIn("systemctl restart \"$monitor_service\"",s)
        self.assertIn("discord-limit-monitor.csv",s)
        self.assertIn("refusing to overwrite it",s)
        self.assertIn("^\"[0-9]{15,22}\"$",s) if False else None
        self.assertIn("^[0-9]{15,22}$",s)
        for forbidden in ("systemctl restart dkcapital-mt5",
                          "systemctl restart sktrader.service",
                          "systemctl stop",
                          "OrderSend",
                          "rm -rf",
                          "EnableNewEntries=true",
                          "discord_limit_30m_signals.csv"):
            self.assertNotIn(forbidden,s)

if __name__=="__main__":
    unittest.main()