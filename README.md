# Sktrader

Minimal JIFU scanner monitoring for DK Capital: **JIFU Auroux Alerts -> Python/Playwright -> SQLite -> Discord**.

**Milestone 1 is WATCH ONLY. The repository contains no MT5 order placement, broker keys or live trade permission.**

## What it does

- Keeps an authenticated Chromium session on the existing VPS.
- Tracks the mixed JIFU Alerts sidebar (Auroux, SniperPro, WolfX, Harmonics etc.) every 5 seconds without reloading the chart, but only forwards AUROUX XAUUSD.
- Validates XAUUSD, BUY/SELL, timeframe, point of interest, invalidation and both exits.
- Saves a baseline on first launch, so old alerts are not posted as new trades.
- Detects new AUROUX XAUUSD cards by comparing the entire ordered mixed-provider feed, including surrounding non-Auroux cards for overlap.
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

The initial log should report `Baseline saved: N mixed-scanner feed cards (M Auroux XAUUSD)`. **Historical cards are intentionally not sent**. New cards produce Discord notifications. Run `python -m unittest discover -s tests -v` for offline tests.

After a successful interactive test, install the example systemd service at `deploy/sktrader.service.example`, substituting the actual Linux user and paths. Only one process may use the browser profile at once.

## Configuration

| Variable | Default |
|---|---|
| `SKTRADER_POLL_SECONDS` | `5` |
| `SKTRADER_TIMEFRAME` | `ALL` (every Auroux timeframe; individual card per signal) |
| `SKTRADER_LEVELS` | `250,500` |
| `SKTRADER_HEADLESS` | `true` |
| `SKTRADER_DISCORD_CHANNEL_ID` | Existing DK Capital Discord bot destination, `1565508564775075981` |
| `SKTRADER_DISCORD_BOT_TOKEN_FILE` | Private local file containing existing bot token, readable only by the Sktrader Linux user |
| `SKTRADER_BROWSER_PROFILE` | Local persistent browser state |
| `SKTRADER_DATABASE` | Local SQLite alert journal |

## One-time historical replay of the currently loaded alert cards

The normal `watch` command intentionally baselines older cards without
posting them. To send **currently loaded** AUROUX XAUUSD cards to Discord
once, use `python -m sktrader replay-visible` under the **same environment
and Linux user as watch**. This command is manual, never runs automatically
on service startup, and does not place any trades.

**Stop `watch` using Ctrl+C before replaying.** The profile-wide lock will
reject simultaneous `login`, `watch`, `diagnose` and `replay-visible`.
Run the following from your existing VPS root SSH terminal:

```bash
cd /opt/sktrader
runuser -u dkmt5c -- bash -c '
set -a; source /opt/sktrader/.env; set +a
export HOME=/var/lib/dkcapital-mt5-3
export DISPLAY=:155
export XAUTHORITY=/var/lib/dkcapital-mt5-3/.local/share/sktrader/Xauthority-155
export PLAYWRIGHT_BROWSERS_PATH=/opt/sktrader/.browsers
exec /opt/sktrader/.venv/bin/python -u -m sktrader replay-visible
'
```

The command scrolls the Alerts sidebar through older pages (up to 60 small
scroll increments by default), preserving an ordered archive of card text.
It checks each page for completeness and requires at least two-card overlap
when the website replaces a virtualised page. It then filters to AUROUX
XAUUSD and the configured timeframe/ranges. Each eligible card is sent
separately, oldest first, even when prices match. A fresh scanner selection
does not necessarily mean that JIFU's Alerts panel is scanner-filtered.

To scan a longer history, set `SKTRADER_REPLAY_MAX_SCROLLS=120` in the
private local `/opt/sktrader/.env` before replaying (maximum 200).
If the scan reaches the cap or loses history-page overlap, it stops **without
consuming the one-time replay batch**, allowing a safe retry. Where the
sidebar cannot be programmatically scrolled, the command falls back to
the latest cards only and reports the limitation.
Replayed Discord cards are prominently marked **HISTORICAL REPLAY, NOT A
NEW SIGNAL, NO TRADE**.

The whole batch and its SQLite outbox events are recorded atomically.
Repeating the command does not create a second historical replay batch;
after interrupted delivery the pending queued messages are retried. Discord
network errors can still create a rare duplicate, despite the message nonce.

If the scanned mixed-scanner history contains **no AUROUX XAUUSD card**,
the command sends zero messages and does **not** consume the one-time replay.
The browser can only retrieve as much older history as JIFU's sidebar exposes;
it cannot invent missing alerts or retrieve data JIFU no longer makes available.
Scanning can take longer when JIFU fetches or virtualises older cards.

The replay batch stores the **latest (newest) page** as the mixed-feed
cursor, not the older end of the archive, so the subsequent `watch` process
does not mistake just-replayed cards for new ones.
After replay, restart `watch` normally.

## Important limitations

The authenticated JIFU DOM was checked on 29 October 2026. Although the Auroux scanner is selected, the Alerts sidebar can contain HARMONICS, SNIPERPRO, WOLFX and PRIME alerts for unrelated instruments. The collector now accepts mixed-scanner cards as source-feed context and validates only AUROUX XAUUSD for publication. JIFU automated access must be allowed under the subscription terms.

JIFU examples do not expose unique event IDs or precise issuance timestamps. Ordered snapshot comparison helps preserve repeated same-price signals, but the collector deliberately stops trusting a list after an unexplained reordering or complete replacement. The Discord message nonce limits accidental duplicate posts over a short window; durable SQLite state retains publication intent. A duplicate is still possible after longer outages or a crash.

There is **no MT5 integration or automatic trading in this release**. A future GitHub PR will require an isolated Auroux execution adapter, shared portfolio risk budget, broker order reconciliation and explicit live activation. Existing DK Capital EA and orders are not changed.

## Milestone 1 acceptance

Authenticate -> confirm live alert cards parse -> baseline -> receive a fresh signal in Discord -> restart without replay -> verify failed session alerts. No order execution during acceptance.


## Troubleshoot mixed-scanner JIFU Alerts

The **black VNC screen is normal** when `SKTRADER_HEADLESS=true`.
A headless browser can show a valid feed with **zero current AUROUX XAUUSD alerts**. That is not an error. The collector must see all visible sidebar cards to maintain its ordered feed snapshot.

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

**Parser acceptance gate:** The diagnostic reports how many mixed-scanner card boundaries are readable. A mixed feed of 10 cards with zero AUROUX XAUUSD is a valid capture, not a parser failure. Only source `AUROUX` and instrument `XAUUSD` are eligible for publication; a WOLFX/XAUUSD signal must never be forwarded as Auroux. The initial mixed-feed read establishes a baseline and does not replay historical posts. An incomplete feed or malformed new eligible Auroux signal fails closed without advancing the durable snapshot. A new Auroux card should appear in Discord after its insertion into the feed, subject to the configured timeframe and scanner-level filters.

The All filter in JIFU is a **timeframe** filter, not proof that the alert feed is restricted to the selected scanner. If source filtering is supported in the JIFU UI, it can be investigated separately; don't assume it is active.

## Each Auroux card is a distinct signal

A 30M SELL, a 5M SELL and a 1H SELL with the same POI, invalidation,
TP1, TP2 and 250-pip scanner level are **three independent alert cards**.
Sktrader uses an age-independent, card-level feed fingerprint, including
scanner, XAUUSD, direction and timeframe. It must publish **three** separate
Discord messages when these are three new cards, not collapse them into
one signal by price. The first observed list is a historical baseline and
will not be reposted.

Default `SKTRADER_TIMEFRAME=ALL` forwards all supported Auroux timeframes.
An existing local `/opt/sktrader/.env` with `SKTRADER_TIMEFRAME=5M` is
**not** changed by a `git pull`; set it to `ALL` explicitly on the VPS.
This does not place three MT5 orders: execution is disabled and messages
are labelled `ALERT ONLY - NO TRADE`.
