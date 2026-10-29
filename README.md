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

If Chromium reports missing system libraries, install the distribution's Playwright browser dependencies. Edit `.env` locally with your Discord webhook and paths. Never commit JIFU cookies, login credentials, webhooks or the browser profile to GitHub. The repository is **public**.

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
| `SKTRADER_DISCORD_WEBHOOK` | Required for monitoring |
| `SKTRADER_BROWSER_PROFILE` | Local persistent browser state |
| `SKTRADER_DATABASE` | Local SQLite alert journal |

## Important limitations

The precise authenticated JIFU DOM hasn't yet been tested. The extractor works from the *Point of Interest* label up to the smallest complete alert card and may need adjusting during the first logged-in test. JIFU automated access must be allowed under the subscription terms.

JIFU examples do not expose unique event IDs or precise issuance timestamps. Ordered snapshot comparison helps preserve repeated same-price signals, but the collector deliberately stops trusting a list after an unexplained reordering or complete replacement. Discord may occasionally receive a duplicate after a network/crash ambiguity; SQLite keeps the event record.

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
