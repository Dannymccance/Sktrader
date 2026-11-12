# Sktrader

JIFU Alerts collector. It reads the mixed, unfiltered JIFU Alerts sidebar
in one persistent Chromium session, monitoring **XAUUSD from every scanner**
plus **every instrument published by Sniper Pro**. Messages route to a
separate Discord channel per source and, for non-gold Sniper Pro, per symbol.

**Watch-only. No MT5, broker credentials, trading decisions or order placement.**
Non-XAUUSD alerts from all other scanners are ignored.

## Sources and destinations

| Scanner in JIFU | Discord channel |
|---|---|
| AUROUX | Existing channel ID `1565508564775075981` |
| QUANTUM | `#quantum-signals` |
| TREND WAVE | `#trend-wave-signals` |
| NEXUS | `#nexus-signals` |
| SNIPER PRO / SNIPERPRO (XAUUSD) | `#sniper-pro-signals` (existing gold channel) |
| SNIPER PRO (NAS100USD) | `#sniper-pro-nas100usd-signals` (new) |
| SNIPER PRO (US30USD) | `#sniper-pro-us30usd-signals` (new) |
| SNIPER PRO (BTCUSDT) | `#sniper-pro-btcusdt-signals` (new) |
| SNIPER PRO (any future symbol) | Dedicated `#sniper-pro-<symbol>-signals`, created on first delivery |
| WOLF X / WOLFX | `#wolfx-signals` |
| HARMONICS | `#harmonics-signals` |
| PRIME | `#prime-signals` |

The bot posts **one card per new monitored signal**, even for matching prices across
different scanners or timeframes. It includes source, display age, XAUUSD,
timeframe/direction and the signal's actual displayed labels/values.
Different scanners may have extra exits or zone data. Absent fields are
**not invented**. No health chatter goes to the signal channels.

**Gold execution remains isolated.** The Discord-to-MT5 bridge continues to
read only the existing `#sniper-pro-signals` channel ID
`1565527578163552327`. The new channels are notification-only. Do not
add their IDs to any trading bridge or EA.

## JIFU configuration

In the **right-hand Alerts sidebar**, leave the scanner dropdown at
**Select Scanner** (unfiltered), not AUROUX or another individual scanner.
Choose the **All** timeframe filter. The top-left scanner selector on
the chart is unrelated.

A scanner selection change makes the watcher **pause without advancing**
its snapshot, rather than silently skip other scanners. The watcher
does not click source dropdowns or reload JIFU automatically.

## VPS setup

Sktrader is on `/opt/sktrader` on the existing Ubuntu VPS. Before
updating, stop the current `sktrader watch` process with Ctrl+C. **Do not
restart MT5, DK Capital services or Docker.**

```bash
cd /opt/sktrader
git pull --ff-only origin main
```

The existing bot token file stays in
`/var/lib/dkcapital-mt5-3/.local/share/sktrader/discord-bot-token`,
owner-only mode, and must never be pasted into chat or committed to GitHub.
The AUROUX channel remains
`SKTRADER_DISCORD_CHANNEL_ID=1565508564775075981` in `.env`.

### Reuse the eight existing #*-signals channels (no manual IDs required)

The first version of `setup-channels` created seven duplicate `xauusd-*`
channels. Those were not needed because the Discord server already has
`auroux-signals`, `quantum-signals`, `trend-wave-signals`,
`nexus-signals`, `sniper-pro-signals`, `wolfx-signals`,
`harmonics-signals`, and `prime-signals` under Signals.

After updating GitHub `main`, **stop the watcher before running setup**.
The revised `setup-channels` uses the existing bot's Discord GET endpoints
to discover the real IDs for those eight exact names, requiring one of each
in the same Discord category as the configured Auroux channel. It
does **not** create, rename or delete channels, and sends no messages.
`gws-signals` is left alone.

```bash
cd /opt/sktrader
runuser -u dkmt5c -- bash -c '
set -a; source /opt/sktrader/.env; set +a
export HOME=/var/lib/dkcapital-mt5-3
exec /opt/sktrader/.venv/bin/python -u -m sktrader setup-channels
'
```

The command prints all eight verified scanner/channel IDs and updates
`/var/lib/dkcapital-mt5-3/.local/share/sktrader/scanner-channels.json`
privately and atomically. It retains a backup of the prior mapping
(`scanner-channels.legacy.json`), without changing any Discord channels.
If a name is missing or ambiguous, it refuses to overwrite the mapping.
The watcher uses only this validated channel map. No manual ID collection
or sharing credentials is necessary.

The duplicate `xauusd-*` channels may be removed in Discord **after**
testing that the original `*-signals` channels receive the correct
XAUUSD alerts. Removal is a separate manual action, not part of setup.

### Create the new Sniper Pro channels

The DK Capital bot needs **Manage Channels**, **Send Messages** and **Embed Links**
permissions under the current Signals category. Stop the **Sktrader watcher
only** before channel setup because the collector uses a browser-profile lock.
Do not stop MT5, the Discord limit bridge or the separate compiler VNC.

After merging the feature and updating `/opt/sktrader`, run:

```bash
cd /opt/sktrader
runuser -u dkmt5c -- bash -c '
set -a; source /opt/sktrader/.env; set +a
export HOME=/var/lib/dkcapital-mt5-3
exec /opt/sktrader/.venv/bin/python -u -m sktrader setup-sniper-channels
'
```

The command creates or reuses three channels in the same category as the
existing `#sniper-pro-signals`: NAS100USD, US30USD and BTCUSDT. It does
not post alerts, alter the gold channel or change any trading process.
Optionally precreate additional symbol channels by setting
`SKTRADER_SNIPER_SYMBOLS` to a comma-separated list.
Future Sniper Pro symbols automatically get a dedicated channel on their
first new signal. If creation fails, the SQLite event remains pending
for retry and is **never** redirected to the gold trading channel.

### Start the visible watcher

Use `SKTRADER_HEADLESS=false` in `.env` while testing through TigerVNC
on display `:155`. The browser should show **Alerts > Select Scanner > All**.
Run only one watcher per profile:

```bash
cd /opt/sktrader
runuser -u dkmt5c -- bash -c '
set -a; source /opt/sktrader/.env; set +a
export HOME=/var/lib/dkcapital-mt5-3
export DISPLAY=:155
export XAUTHORITY=/var/lib/dkcapital-mt5-3/.local/share/sktrader/Xauthority-155
export PLAYWRIGHT_BROWSERS_PATH=/opt/sktrader/.browsers
exec /opt/sktrader/.venv/bin/python -u -m sktrader watch
'
```

The first complete mixed-feed read establishes a baseline and does **not**
post older alerts. New XAUUSD scanner cards go to their original scanner
channels. New non-gold Sniper Pro cards go to dedicated instrument channels.
All other non-gold scanner-market combinations remain excluded. Poll interval defaults to five seconds,
configured by `SKTRADER_POLL_SECONDS`.

## Optional one-time historical replay

Stop the watcher first. Run the same environment as above but use
`replay-visible` instead of `watch`. It makes bounded scrolling attempts
through the JIFU Alerts sidebar, preserving history only when successive
windows overlap safely. If the scan reaches its configured limit or loses
history-page overlap, it fails before posting. If no monitored cards are found,
the one-time allowance is not consumed.

The multi-scanner replay uses its own durable SQLite batch and does not
automatically run when monitoring starts. Old alerts may no longer be
actionable. Messages contain the original scanner-card fields only.

## Guarantees and limitations

- Each card is fingerprinted without its changing relative age. Identical
  level prices do not merge different scanner/timeframe cards.
- If the mixed list reorders without enough overlap, the reader pauses
  rather than inventing event identities. JIFU does not provide stable
  event IDs here.
- If a newly published monitored card has an unsupported scanner or malformed
  structure, the current pass is rejected, the cursor is preserved and
  the diagnostic is written only to local logs.
- Discord posting uses authenticated REST calls as the **same existing bot**;
  it does not launch a second bot or change the DK Capital trading runtime.
- Durable pending events are retried after a crash; a duplicate remains
  possible after an ambiguous Discord network response.
- Systemd deployment should be configured after observing successful live
  monitored signal delivery to the correct channels.

Run `/opt/sktrader/.venv/bin/python -m unittest discover -s tests -v`
for offline tests. JIFU automated access must comply with the subscriber terms.
