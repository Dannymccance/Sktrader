"""JIFU collector recovery and bounded health alerts; never executes trades."""
from __future__ import annotations

import json
import os
import stat
import time
import urllib.request
from pathlib import Path

LOGIN_LABELS = ("log in", "sign in", "login")


def login_state(diagnostic: dict) -> str:
    """Require explicit login form evidence before any credential submission."""
    if diagnostic.get("possible_login") and not diagnostic.get("has_alerts_tab"):
        return "login"
    if diagnostic.get("has_alerts_tab"):
        return "dashboard"
    return "unknown"


def private_credentials(path: Path) -> dict[str, str]:
    info = path.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or (info.st_mode & 0o077):
        raise PermissionError("JIFU credentials must be owned by collector and mode 0600/0400")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or not isinstance(value.get("email"), str) or not isinstance(value.get("password"), str):
        raise ValueError("JIFU credential file must contain email and password strings")
    if not value["email"].strip() or not value["password"]:
        raise ValueError("JIFU credentials missing")
    return value


def try_login(page, path: Path) -> bool:
    """Submit only an unambiguous email/password form. Never bypass 2FA/CAPTCHA."""
    from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
    creds = private_credentials(path)
    password = page.locator('input[type="password"]:visible')
    if password.count() != 1:
        raise RuntimeError("Ambiguous JIFU password field: interactive authentication required")
    form = password.locator("xpath=ancestor::form[1]")
    if form.count() != 1:
        raise RuntimeError("JIFU login form not identified: interactive authentication required")
    # Username is allowed: do not require an email-address field.
    user = form.locator(
        'input[type="email"]:visible, input[autocomplete="username"]:visible, '
        'input[name="username"]:visible, input[name="login"]:visible, '
        'input[name="email"]:visible, input[id="username"]:visible, input[id="email"]:visible'
    )
    if user.count() != 1:
        raise RuntimeError("Ambiguous JIFU username field: interactive authentication required")
    user.fill(creds["email"])
    password.fill(creds["password"])
    buttons = form.get_by_role("button", name=__import__("re").compile(r"^(sign in|log in|login)$", __import__("re").I))
    if buttons.count() != 1:
        raise RuntimeError("Ambiguous JIFU sign-in button: interactive authentication required")
    buttons.click(timeout=10000)
    try:
        page.wait_for_url("**/jifu/dashboard**", timeout=15000)
    except PlaywrightTimeoutError:
        return False
    return True


class HealthMonitor:
    """A distinct admin-only Discord destination; never signal channels."""
    def __init__(self, config):
        self.channel = int(os.getenv("SKTRADER_HEALTH_CHANNEL_ID", "0") or "0")
        self.config = config
        self.degraded_since = None
        self.sent_degraded = False
        self.last_report = 0.0
        self.last_good = None

    def healthy(self):
        self.last_good = time.monotonic()
        if self.degraded_since is not None:
            if self.sent_degraded:
                self._send("JIFU scanner monitoring recovered. XAUUSD feed is readable again.")
            self.degraded_since = None
            self.sent_degraded = False

    def degraded(self, reason: str):
        now = time.monotonic()
        if self.degraded_since is None:
            self.degraded_since = now
        if now - self.degraded_since >= 120 and (not self.sent_degraded or now - self.last_report >= 3600):
            self._send("JIFU scanner monitoring interrupted for at least 2 minutes. Check Sktrader service logs. Reason: " + reason[:120])
            self.sent_degraded = True
            self.last_report = now

    def _send(self, content: str):
        if not self.channel:
            return
        # Do not send health notices to any of the eight signal channels.
        try:
            from sktrader.watcher import _bot_token
            token = _bot_token(self.config.bot_token_file)
            payload = json.dumps({"content": content, "allowed_mentions": {"parse": []}}).encode()
            req = urllib.request.Request(
                f"https://discord.com/api/v10/channels/{self.channel}/messages",
                data=payload, method="POST",
                headers={"Authorization": "Bot " + token, "Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=10):
                pass
        except Exception as exc:
            # Admin channel errors must never interrupt collecting signals.
            __import__("logging").getLogger("sktrader").warning(
                "JIFU admin health notification failed (%s)", type(exc).__name__
            )
