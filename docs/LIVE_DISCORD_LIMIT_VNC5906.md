# Live Discord channel 1565527578163552327 -> unified MT5 EA on VNC 5906

The current installation has a clean-compiled and attached
`DiscordLimitUnified.ex5` v2.01 and publishes fresh broker telemetry
from the isolated `dkvnc` MT5 terminal. The separate Discord Gateway
monitor has successfully logged a real human-authored XAUUSD SELL
signal. That monitor intentionally writes **outside** MT5.

This release creates a **separate production listener**, with
persistent deduplication and an explicit activation timestamp. The
production listener uses the same tested Discord bot token, but accepts
only these previously verified authors:

- DK Capital app bot `1554225452091048017` (historical scanner feed).
- Danny `508740193369587722` (manually posted test signals).

Every new eligible XAUUSD signal has its **POI rounded to two decimal
places before writing the MT5 CSV**. This was explicitly requested after
the EA rejected `4167.52875` as off-grid on MetaQuotes-Demo. For example,
`4167.52875 -> 4167.53` and `3067.52875 -> 3067.53`. The bridge uses
decimal ROUND_HALF_UP so half-cent cases are deterministic. Original
Discord messages are unchanged. The EA independently checks the broker
tick grid; its $7.50 adverse SL, $15 favourable TP, 4% balance risk,
30-minute expiry and order direction are unchanged. A broker with a
coarser tick grid may still reject a two-decimal price.

**Deployment after the two-decimal fix:** run
`cd /opt/sktrader && git pull --ff-only origin main`, then
`sudo systemctl restart sktrader-discord-limit-live.service`.
The service retains its production database and cutover timestamp.
**Do not rerun `stage` or `start`**, which deliberately refuse an
existing live database/service. Previously received signal IDs are
not re-sent or repriced; send a **fresh** new signal to test. The
monitor service can be restarted separately if desired, but it does
not write to MT5.

Every other author and channel is ignored. The bot's Message Content
Intent has already been enabled, as confirmed by successful Gateway
connections with the human author, but it must remain enabled.

## Deploy production signal feed

First in the MT5 GUI on VNC 5906, select the attached
`DiscordLimitUnified` EA inputs and set `EnableNewEntries=false`
and global **Algo Trading OFF**. A prior screenshot showed
`EnableNewEntries=true`. The listener service cannot inspect or
force-reset that graphical input itself.

In VPS root SSH:

```bash
cd /opt/sktrader
git pull --ff-only origin main
sudo bash deploy/live-discord-limit-vnc5906.sh inspect
sudo bash deploy/live-discord-limit-vnc5906.sh stage
```

The `stage` action requires fresh account telemetry, the working VNC,
the verified existing Discord token and active quarantine monitor. It
uses a read-only Discord REST call to confirm the bot and source channel.
It creates a new, standalone systemd service but **does not start** it.
The service's `--not-before-ms` argument is the deployment UTC cutoff.
It will not accept any older messages, even those still inside a 30m
TTL or replayed on a resumed Gateway session. The production database
is separate from the monitoring database.

After reviewing the new service and confirming no unwanted old
`discord_limit_30m_signals.csv` is present, run:

```bash
sudo bash deploy/live-discord-limit-vnc5906.sh start
sudo bash deploy/live-discord-limit-vnc5906.sh status
sudo journalctl -u sktrader-discord-limit-live.service -n 40 --no-pager
```

Expected: `Discord Gateway connected; channel 1565527578163552327,
trusted authors 1554225452091048017,508740193369587722`.
The live service automatically starts on reboot. It writes
`discord_limit_30m_signals.csv` atomically into the **correct**
MT5 Common/Files directory:

```text
/var/lib/dkcapital-desktop/.wine-mt5/drive_c/users/dkvnc/AppData/Roaming/MetaQuotes/Terminal/Common/Files/discord_limit_30m_signals.csv
```

An initial **empty** signal file is normal. No prior monitor CSV or
messages are copied into it. Only *new* eligible Discord messages
created after the explicit cutoff are processed.

## Arm MT5 only after the feed is connected

On your connected **DEMO** MT5 terminal, verify the chart symbol is
`XAUUSD` or the broker's valid XAUUSD-suffixed contract. Inspect the
broker's tick size, minimum lot, limit order constraints, hedging mode,
and specified pending-order expiration support. If an exact POI cannot
be represented on the broker's tick grid, the EA deliberately ignores
that signal rather than rounding it. The previous manual test POI
`3067.52875` is unlikely to be valid on a 0.01 gold tick grid.

In `DiscordLimitUnified` inputs, set `EnableNewEntries=true`.
Enable MT5 Algo Trading and the EA's trading permission. **At that
point a new valid, authorized signal can place a broker order.** Do
not reuse the old test message, as it predates the cutoff.

Trading behavior: BUY_LIMIT or SELL_LIMIT at exact POI, broker
expiration 30 minutes from original Discord message creation, $7.50
adverse SL, $15 favorable TP, 4% of current account balance risk
calculated using `OrderCalcProfit` and floored lot increments, no
aggregate risk cap. These are nominal stop-risk figures; slippage,
fees and gaps can result in larger losses. Five simultaneous 4%-risk
orders imply roughly 20% of balance at risk. Hedging is necessary
for the strategy's independent overlapping SL/TP positions; on
netting/exchange accounts the EA provides telemetry only.

Confirm a **fresh** eligible signal at both ends: Gateway log
`Limit signal queued:`, then EA Experts/Journal either
`LIMIT placed ... order=...` or a specific broker rejection
reason. In MT5 Trade tab verify the actual broker-native pending order
ticket, exact price/SL/TP/expiration and volume. Do not claim full
live execution before that confirmation.

To stop the feed immediately, run:

```bash
sudo systemctl stop sktrader-discord-limit-live.service
```

This only stops *future signal ingestion*. **It does not cancel any
existing pending orders or positions.** Disable EA entries in MT5,
then inspect and manually manage existing orders as required. Leave
other MT5, GWS, AUROUX, JIFU and compiler services untouched.

For a future restart, use `sudo systemctl restart
sktrader-discord-limit-live.service`; **do not rerun the stage
action**, which intentionally refuses to overwrite a previously
commissioned live database or unit. Persistent SQLite deduplication
remains in place across reconnects.
