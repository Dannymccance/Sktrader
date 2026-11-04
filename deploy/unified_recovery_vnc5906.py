"""Bounded automatic recovery for the isolated VNC 5906 Discord -> MT5 stack.

Run as root via a 30-second systemd timer. Never place/modify/cancel trades,
never kill Wine/MT5 while it is running, and never touch legacy trading instances.
"""
from __future__ import annotations

import argparse
import csv
import fcntl
import json
import logging
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import urllib.request

LOG = logging.getLogger("dkcapital-recovery")
HOME = Path("/var/lib/dkcapital-desktop")
PRIVATE = HOME / ".local/share/sktrader"
COMMON = HOME / ".wine-mt5/drive_c/users/dkvnc/AppData/Roaming/MetaQuotes/Terminal/Common/Files"
HEALTH = PRIVATE / "discord-limit-live-health.json"
SIGNALS = COMMON / "discord_limit_30m_signals.csv"
TELEMETRY = COMMON / "discord_limit_account.csv"
LIVE = "sktrader-discord-limit-live.service"
VNC = "dkcapital-desktop-vnc.service"
JIFU = "sktrader.service"
STATE_DIR = Path("/var/lib/dkcapital-recovery")
STATE_PATH = STATE_DIR / "watchdog.json"
LOCK_PATH = Path("/run/lock/dkcapital-recovery-vnc5906.lock")
ALERT_CONFIG = Path("/etc/dkcapital-recovery-admin.conf")
SOURCE_CHANNEL = "1565527578163552327"
BOT_AUTHOR = "1554225452091048017"
HUMAN_AUTHOR = "508740193369587722"


def run(*args: str, timeout: int = 12) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout, check=False)


def active(unit: str) -> bool:
    return run("systemctl", "is-active", "--quiet", unit).returncode == 0


def mt5_running() -> bool:
    # Match only the isolated desktop user, including Windows-style Wine process titles.
    return run("pgrep", "-u", "dkvnc", "-f", r"terminal64[.]exe").returncode == 0


def ready_gateway(health: dict, now: float, *, expected_pid: int | None = None) -> bool:
    try:
        interval = float(health["heartbeat_interval_s"])
        updated = float(health["updated_at"])
        ack = float(health["last_ack_at"])
        pid = int(health["pid"])
        if not (5 <= interval <= 300 and pid > 0):
            return False
        if expected_pid and pid != expected_pid:
            return False
        # No new Discord message is needed to be healthy. Discord ACKs are the probe.
        maximum_age = max(100.0, 2.5 * interval + 15.0)
        return (health["status"] == "ready"
                and 0 <= now - updated <= maximum_age
                and 0 <= now - ack <= maximum_age)
    except (KeyError, ValueError, TypeError, OverflowError):
        return False


def telemetry_age(now: float) -> tuple[float, str] | None:
    try:
        with TELEMETRY.open(encoding="utf-8-sig", newline="") as f:
            rows = list(csv.DictReader(f, delimiter=";"))
        if len(rows) != 1:
            return None
        a = rows[0]
        stamp = float(a["generated_at"])
        if int(a["login"]) <= 0 or a["mode"] not in {"DEMO", "CONTEST", "REAL"}:
            return None
        return now - stamp, f'{a["mode"]} login={a["login"]} server={a["server"]}'
    except (OSError, ValueError, KeyError, TypeError):
        return None


def bounded_attempt(state: dict, key: str, now: float, *, gap: int = 240, cap: int = 3) -> bool:
    item = state.setdefault("components", {}).setdefault(key, {})
    history = [float(t) for t in item.get("attempts", []) if 0 <= now - float(t) < 3600]
    item["attempts"] = history
    if len(history) >= cap or (history and now - history[-1] < gap):
        return False
    item["attempts"].append(now)
    return True


def note(state: dict, key: str, ok: bool, description: str, now: float) -> int:
    item = state.setdefault("components", {}).setdefault(key, {})
    prior = bool(item.get("degraded", False))
    if ok:
        item["failures"] = 0
        item["degraded"] = False
        if prior:
            alert(state, f"RECOVERED: {description}", now)
        return 0
    item["failures"] = int(item.get("failures", 0)) + 1
    LOG.warning("%s unavailable for %s consecutive checks", description, item["failures"])
    if item["failures"] >= 2 and not prior:
        item["degraded"] = True
        alert(state, f"DOWN: {description}", now)
    return item["failures"]


def admin_channel() -> str | None:
    if not ALERT_CONFIG.exists():
        return None
    st = ALERT_CONFIG.stat()
    if st.st_uid != 0 or (st.st_mode & 0o077) or not ALERT_CONFIG.is_file():
        LOG.error("Admin channel config must be root-owned and owner-only")
        return None
    match = re.fullmatch(r"RECOVERY_ADMIN_CHANNEL_ID=([0-9]{15,22})\s*",
                         ALERT_CONFIG.read_text(encoding="ascii"))
    if not match:
        LOG.error("Admin channel configuration is invalid")
        return None
    value = match.group(1)
    if value == SOURCE_CHANNEL:
        LOG.error("Refusing to send recovery messages to live trading signal channel")
        return None
    return value


def alert(state: dict, message: str, now: float) -> None:
    LOG.warning("RECOVERY ALERT: %s", message)
    channel = admin_channel()
    if not channel:
        return
    token_path = PRIVATE / "discord-bot-token"
    try:
        token_stat = token_path.stat()
        if token_stat.st_mode & 0o077:
            raise PermissionError("Discord token is not owner-only")
        token = token_path.read_text(encoding="utf-8").strip()
        payload = json.dumps({
            "content": "[DK Capital watchdog] " + message[:1200],
            "allowed_mentions": {"parse": []}
        }).encode("utf-8")
        req = urllib.request.Request(
            f"https://discord.com/api/v10/channels/{channel}/messages",
            headers={"Authorization": "Bot " + token,
                     "Content-Type": "application/json",
                     "User-Agent": "dkcapital-recovery/1.0"},
            data=payload, method="POST",
        )
        with urllib.request.urlopen(req, timeout=8) as response:
            if response.status not in (200, 201, 204):
                raise RuntimeError(f"HTTP {response.status}")
    except Exception as exc:
        LOG.warning("Recovery notification failed: %s", type(exc).__name__)


def checked_unit() -> bool:
    path = Path("/etc/systemd/system") / LIVE
    try:
        txt = path.read_text(encoding="utf-8")
    except OSError:
        return False
    return all(fragment in txt for fragment in (
        "User=dkvnc",
        "--channel-id " + SOURCE_CHANNEL,
        "--author-id " + BOT_AUTHOR,
        "--author-id " + HUMAN_AUTHOR,
        "--not-before-ms ",
        "--output " + str(SIGNALS),
        "--database " + str(PRIVATE / "discord-limit-live.db"),
    ))


def gateway_state(now: float) -> bool:
    if not checked_unit() or not active(LIVE):
        return False
    try:
        h = json.loads(HEALTH.read_text(encoding="ascii"))
        pid_result = run("systemctl", "show", LIVE, "-p", "MainPID", "--value")
        pid = int(pid_result.stdout.strip()) if pid_result.returncode == 0 else 0
        return pid > 0 and ready_gateway(h, now, expected_pid=pid)
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return False


def vnc_unit_verified() -> bool:
    try:
        t = (Path("/etc/systemd/system") / VNC).read_text(encoding="utf-8")
        return "User=dkvnc" in t and "-rfbport 5906 -localhost yes" in t
    except OSError:
        return False


def launch_mt5() -> bool:
    launcher = HOME / "bin/mt5"
    exe = HOME / ".wine-mt5/drive_c/Program Files/MetaTrader 5/terminal64.exe"
    if not (launcher.is_file() and os.access(launcher, os.X_OK) and exe.is_file()):
        LOG.error("Isolated MT5 launcher or terminal64.exe unavailable")
        return False
    if mt5_running():
        return False
    # Transient unit runs strictly as dkvnc and does not auto-restart an already
    # running terminal. Subsequent watchdog checks verify telemetry independently.
    unit = "dkcapital-mt5-recovery-" + str(int(time.time()))
    result = run(
        "systemd-run", "--collect", "--unit=" + unit,
        "--property=User=dkvnc", "--property=Group=dkvnc",
        "--property=WorkingDirectory=" + str(HOME),
        "--setenv=HOME=" + str(HOME),
        "--setenv=DISPLAY=:5",
        "--setenv=WINEPREFIX=" + str(HOME / ".wine-mt5"),
        "--setenv=WINEARCH=win64",
        str(launcher), timeout=20,
    )
    if result.returncode:
        LOG.error("MT5 isolated launch failed: %s", result.stderr.strip()[:240])
        return False
    LOG.warning("Started isolated MT5 launcher as dkvnc via %s", unit)
    return True


def load_state() -> dict:
    try:
        state = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        if isinstance(state, dict) and isinstance(state.get("components"), dict):
            return state
    except (OSError, json.JSONDecodeError, ValueError):
        pass
    return {"components": {}}


def save_state(state: dict) -> None:
    STATE_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    tmp = STATE_PATH.with_suffix(".new")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="ascii") as f:
            json.dump(state, f, sort_keys=True)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, STATE_PATH)
    finally:
        if tmp.exists():
            tmp.unlink()


def cycle(state: dict, *, now: float) -> None:
    if not checked_unit():
        alert(state, "Unexpected live Discord unit; recovery disabled", now)
        return
    gateway_ok = gateway_state(now)
    gateway_fail = note(state, "gateway", gateway_ok, "Discord Gateway heartbeat", now)
    if gateway_fail >= 2 and bounded_attempt(state, "gateway_restart", now):
        LOG.warning("Restarting only the authorized live Discord listener")
        result = run("systemctl", "restart", LIVE, timeout=20)
        if result.returncode:
            LOG.error("Discord listener restart failed: %s", result.stderr.strip()[:250])

    present = mt5_running()
    display_ok = active(VNC)
    display_fail = note(state, "vnc", display_ok, "VNC 5906 desktop", now)
    if not display_ok and not present and display_fail >= 2 and vnc_unit_verified() and bounded_attempt(state, "vnc_restart", now, gap=300):
        LOG.warning("Restarting isolated VNC 5906 with no MT5 process present")
        result = run("systemctl", "restart", VNC, timeout=30)
        if result.returncode:
            LOG.error("VNC 5906 restart failed: %s", result.stderr.strip()[:250])

    last = telemetry_age(now)
    fresh = last is not None and 0 <= last[0] <= 90
    mt5_fail = note(state, "mt5", present and fresh, "MT5 broker telemetry", now)
    if present and mt5_fail >= 2:
        LOG.warning("MT5 is running but telemetry is stale; refusing to kill/restart a potentially trading terminal")
    elif not present and mt5_fail >= 2 and display_ok and gateway_ok:
        # Gate on a newly rewritten CSV so no old 30-minute input is replayed.
        try:
            feed_fresh = 0 <= now - SIGNALS.stat().st_mtime <= 120
        except OSError:
            feed_fresh = False
        if feed_fresh and bounded_attempt(state, "mt5_launch", now, gap=300):
            launch_mt5()
        elif not feed_fresh:
            LOG.warning("MT5 relaunch blocked: signal file is missing/stale")

    # The JIFU collector has its own recovery and restart policy. Monitor but
    # never restart it here or change its login/session/browser configuration.
    note(state, "jifu", active(JIFU), "JIFU collector service", now)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("check", "status"))
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if os.geteuid() != 0:
        parser.error("Run with sudo")
    STATE_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    state = load_state()
    if args.mode == "status":
        now = time.time()
        print(json.dumps({
            "gateway_healthy": gateway_state(now),
            "live_listener_active": active(LIVE),
            "mt5_running": mt5_running(),
            "mt5_telemetry": telemetry_age(now),
            "vnc_active": active(VNC),
            "jifu_active": active(JIFU),
            "components": state.get("components", {}),
            "admin_discord_alerts_configured": bool(admin_channel()),
        }, indent=2, sort_keys=True))
        return 0
    lock = os.open(LOCK_PATH, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            LOG.warning("Previous recovery cycle still running; skip overlapping check")
            return 0
        cycle(state, now=time.time())
        save_state(state)
        return 0
    finally:
        os.close(lock)


if __name__ == "__main__":
    sys.exit(main())
