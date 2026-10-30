# Sktrader

JIFU **XAUUSD-only** Alerts collector. It reads the mixed, unfiltered JIFU
Alerts sidebar in one persistent Chromium session, identifies the scanner
that published each card, and sends the original signal fields to a
**separate Discord channel per scanner** using the existing DK Capital bot.

**Watch-only. No MT5, broker credentials, trading decisions or order placement.**
All other symbols (BTCUSDT, XAGUSD, NAS100USD, USDJPY, etc.) are ignored.

## Sources and destinations

| Scanner in JIFU | Discord channel |
|---|---|
| AUROUX | Existing channel ID `1565508564775075981` |
| QUANTUM | `#xauusd-quantum` |
| TREND WAVE | `#xauusd-trend-wave` |
| NEXUS | `#xauusd-nexus` |
| SNIPER PRO / SNIPERPRO | `#xauusd-sniper-pro` |
| WOLF X / WOLFX | `#xauusd-wolf-x` |
| HARMONICS | `#xauusd-harmonics` |
| PRIME | `#xauusd-prime` |

The bot posts **one card per new signal**, even for matching prices across
different scanners or timeframes. It includes source, display age, XAUUSD,
timeframe/direction and the signal's actual displayed labels/values.
Different scanners may have extra exits or zone data. Absent fields are
**not invented**. No health chatter goes to the signal channels.

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

### Create scanner channels once

The bot must have **Manage Channels** permission in the same Discord server
as AUROUX. The following command reads the AUROUX channel's server/category,
reuses any existing exact-name channels in that category and creates only
the seven missing text channels. It does not send alerts or alter DK
Capital services.

```bash
cd /opt/sktrader
runuser -u dkmt5c -- bash -c '
set -a; source /opt/sktrader/.env; set +a
export HOME=/var/lib/dkcapital-mt5-3
exec /opt/sktrader/.venv/bin/python -u -m sktrader setup-channels
'
```

The command writes private file
`/var/lib/dkcapital-mt5-3/.local/share/sktrader/scanner-channels.json`,
with all eight distinct channel IDs. If the bot lacks Manage Channels,
create the seven named channels manually and write that same JSON mapping
locally with restrictive permissions (owner `dkmt5c`, mode 0600), using
only real Discord channel IDs. **Never map two scanners to one channel**.
The watcher refuses to start when the mapping is absent, incomplete or
has a wrong AUROUX ID.

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
post older alerts. Subsequent newly published **XAUUSD** scanner cards
are sent only to their mapped scanner channel, using the existing
DK Capital Discord bot. All other symbols and other scanner-market
combinations are not posted. Poll interval defaults to five seconds,
configured by `SKTRADER_POLL_SECONDS`.

## Optional one-time historical replay

Stop the watcher first. Run the same environment as above but use
`replay-visible` instead of `watch`. It makes bounded scrolling attempts
through the JIFU Alerts sidebar, preserving history only when successive
windows overlap safely. If the scan reaches its configured limit or loses
history-page overlap, it fails before posting. If no XAUUSD cards are found,
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
- If a newly published XAUUSD card has an unsupported scanner or malformed
  structure, the current pass is rejected, the cursor is preserved and
  the diagnostic is written only to local logs.
- Discord posting uses authenticated REST calls as the **same existing bot**;
  it does not launch a second bot or change the DK Capital trading runtime.
- Durable pending events are retried after a crash; a duplicate remains
  possible after an ambiguous Discord network response.
- Systemd deployment should be configured after observing successful live
  XAUUSD delivery to the correct channels.

Run `/opt/sktrader/.venv/bin/python -m unittest discover -s tests -v`
for offline tests. JIFU automated access must comply with the subscriber terms.
