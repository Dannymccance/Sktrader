# Sktrader

Minimal JIFU scanner monitoring for DK Capital: **JIFU Auroux Alerts -> Python/Playwright -> SQLite -> Discord**.

**Milestone 1 is WATCH ONLY. The repository contains no MT5 order placement, broker keys or live trade permission.**

## What it does

- Keeps an authenticated Chromium session on the existing VPS.
- Reads Auroux alert cards without reloading the chart every 5 seconds (configurable).
- Validates XAUUSD, BUY/SELL, timeframe, point of interest, invalidation and both exits.
- Saves a baseline on first launch, so old alerts are not posted as new trades.
- Detects newly inserted cards even when the same prices have appeared before.
- Records alerts and Discord delivery status in SQLite for restart recovery.
- Posts new eligible alerts to Discord, explicitly marked **ALERT ONLY - NO TRADE**.
- Fails closed on missing or malformed alerts and warns on repeated read failures.

## Install on VPS

Run as the SAME Linux user that owns the VNC desktop:

```bash
git clone https://github.com/Dannymccance/Sktrader.git /opt/sktrader
cd /opt/sktrader
git switch feat/jifu-auroux-collector
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m playwright install chromium
cp .env.example .env
chmod 600 .env
```

If Chromium reports missing system libraries, install the distribution's Playwright browser dependencies. Edit `.env` locally with the existing DK Capital Discord channel ID, secure bot-token file path and browser paths. Never commit JIFU cookies, login credentials, Discord bot tokens or the browser profile to GitHub. The repository is **public**.

### Log in through VNC

In a terminal on the VNC desktop:

```bash
cd /opt/sktrader
set -a; . ./.env; set +a
.venv/bin/python -m sktrader login
```

Log in manually in the opened Chromium window, select the **Auroux** scanner, open **Alerts**, choose **All**, and press Enter in the terminal to close the browser and save the session.

### Monitor

```bash
cd /opt/sktrader
set -a; . ./.env; set +a
.venv/bin/python -m sktrader watch
```

The initial log should report `Baseline saved: N existing alerts`. **Historical cards are intentionally not sent**. New cards produce Discord notifications. Run `python -m unittest discover -s tests -v` for offline tests.

After a successful interactive test, install the example systemd service at `deploy/sktrader.service.example`, substituting the actual Linux user and paths. Only one process may use the browser profile at once.

## Configuration

| Variable | Default |
|---|---|
| `SKTRADER_POLL_SECONDS` | `5` |
| `SKTRADER_TIMEFRAME` | `5M` |
| `SKTRADER_LEVELS` | `250,500` |
| `SKTRADER_HEADLESS` | `true` |
| `SKTRADER_DISCORD_CHANNEL_ID` | Existing DK Capital Discord bot destination, `1565508564775075981` |
| `SKTRADER_DISCORD_BOT_TOKEN_FILE` | Private local file containing existing bot token, readable only by the Sktrader Linux user |
| `SKTRADER_BROWSER_PROFILE` | Local persistent browser state |
| `SKTRADER_DATABASE` | Local SQLite alert journal |

## Important limitations

The precise authenticated JIFU DOM hasn't yet been tested. The extractor works from the *Point of Interest* label up to the smallest complete alert card and may need adjusting during the first logged-in test. JIFU automated access must be allowed under the subscription terms.

JIFU examples do not expose unique event IDs or precise issuance timestamps. Ordered snapshot comparison helps preserve repeated same-price signals, but the collector deliberately stops trusting a list after an unexplained reordering or complete replacement. The Discord message nonce limits accidental duplicate posts over a short window; durable SQLite state retains publication intent. A duplicate is still possible after longer outages or a crash.

There is **no MT5 integration or automatic trading in this release**. A future GitHub PR will require an isolated Auroux execution adapter, shared portfolio risk budget, broker order reconciliation and explicit live activation. Existing DK Capital EA and orders are not changed.

## Milestone 1 acceptance

Authenticate -> confirm live alert cards parse -> baseline -> receive a fresh signal in Discord -> restart without replay -> verify failed session alerts. No order execution during acceptance.


## Troubleshoot "No complete Auroux alert cards detected"

The **black VNC screen is normal** when `SKTRADER_HEADLESS=true`.
The actual collector failure is the missing DOM alert cards.

Stop the running watcher with Ctrl+C before starting a second process using the
same Chromium browser profile. The watchdog/monitor must not be running.

Run the one-off safe diagnostic using the SAME environment as `watch`:

```bash
cd /opt/sktrader
runuser -u dkmt5c -- bash -c '
  set -a; source /opt/sktrader/.env; set +a
  export HOME=/var/lib/dkcapital-mt5-3
  export DISPLAY=:155
  export XAUTHORITY=/var/lib/dkcapital-mt5-3/.local/share/sktrader/Xauthority-155
  export PLAYWRIGHT_BROWSERS_PATH=/opt/sktrader/.browsers
  exec /opt/sktrader/.venv/bin/python -m sktrader diagnose
'
```

The diagnostic prints **only** page-path, HTML read-state, field-presence
booleans and counts. It does not print JIFU page content, secrets or screenshots.

- `possible_login=true`: browser session may have expired; reauthenticate
  through VNC with `python -m sktrader login`.
- `poi_labels=0`: JIFU may not have finished loading or Alerts is not open.
- `poi_labels>0`, but zero complete cards: the JIFU DOM structure or field
  text differs from the current extractor; update the parser through a PR.
- `Alert capture: READY`: the DOM extractor has parsed the visible alerts;
  close the diagnostic before running the normal `watch` process.

No manual refresh loop or MT5 change is needed to troubleshoot.

## Reuse existing DK Capital Discord bot (no webhook, no second bot)

Sktrader sends `POST /api/v10/channels/1565508564775075981/messages`
as the **existing DK Capital bot** via its token and the Discord REST API.
It does not connect another Discord Gateway session, launch another bot
process, restart DK Capital services, or touch MT5.

Configure the already-installed DK Capital bot to have **View Channel**,
**Send Messages** and **Embed Links** access to channel
`1565508564775075981`. Do not paste its token into chat, GitHub,
process arguments or Sktrader's readable `.env` file.

To create a private token file locally on the VPS using the existing
`/opt/dkcapital/.env` value, run this **as root**. It does not display
the secret and creates a new file readable only by Linux user `dkmt5c`.
It will refuse to overwrite an existing secret file:

```bash
python3 - <<'PY'
import os
import pwd
from pathlib import Path

source = Path("/opt/dkcapital/.env")
target = Path("/var/lib/dkcapital-mt5-3/.local/share/sktrader/discord-bot-token")
lines = source.read_text(encoding="utf-8").splitlines()
matches = [line.split("=", 1)[1].strip().strip('"').strip("'")
           for line in lines if line.lstrip().startswith("DISCORD_BOT_TOKEN=")]
if len(matches) != 1 or len(matches[0]) < 20:
    raise SystemExit("Expected one populated DISCORD_BOT_TOKEN in DKCapital .env")
uid = pwd.getpwnam("dkmt5c").pw_uid
gid = pwd.getpwnam("dkmt5c").pw_gid
fd = os.open(str(target), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
try:
    os.fchown(fd, uid, gid)
    os.write(fd, matches[0].encode("utf-8"))
    os.fchmod(fd, 0o400)
finally:
    os.close(fd)
print("Private bot token file created; token was not displayed")
PY
```

Set in `/opt/sktrader/.env` (the actual token **must not**
appear there):

```dotenv
SKTRADER_DISCORD_CHANNEL_ID=1565508564775075981
SKTRADER_DISCORD_BOT_TOKEN_FILE=/var/lib/dkcapital-mt5-3/.local/share/sktrader/discord-bot-token
```

Remove any obsolete `SKTRADER_DISCORD_WEBHOOK` line to avoid confusion;
webhooks are no longer used. The bot token is validated on watcher startup.
To avoid event duplication, run exactly one Sktrader watcher at a time.

**Parser acceptance gate:** the JIFU chart itself may contain Point of
Interest annotations. The collector now counts only anchors on the right side
of the viewport (the Alerts sidebar), and does not treat chart labels as
individual alert cards. If the number of complete sidebar cards is still less
than the number of sidebar Point of Interest labels, it refuses to establish
a baseline or advance signal history.

Run `python -m sktrader diagnose` and check `document_poi_labels` (entire
page), `excluded_chart_or_other_labels`, `poi_labels` (sidebar only),
and the sidebar `field_coverage` counts. These contain only counts and
booleans, not signal data or secrets. A user interface layout change may
require revisiting the sidebar geometric threshold; never silently fall back
to whole-page matching. Do not enable continuous monitoring unless
`Alert capture: READY` is reported for the entire sidebar.
