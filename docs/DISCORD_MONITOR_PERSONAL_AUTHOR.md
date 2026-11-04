# Diagnose an ignored test signal posted by a person

The non-trading Discord Gateway monitor is connected to channel
`1565527578163552327`, but by default it accepts only bot messages from
ID `1554225452091048017`. A test screenshot showed a human account
named "Danny" posting a `5M - SELL` signal with POI `3067.52875`.
Therefore the monitor excludes that message based on author ID.

The signal parser accepts the plain-text structure
`XAUUSD\n5M - SELL\nPoint of Interest\n3067.52875`, provided the
message was created within 30 minutes and the sender is trusted.

## 1. Identify the Discord message author

Run on VPS root SSH:

```bash
cd /opt/sktrader
git pull --ff-only origin main
sudo bash deploy/configure-discord-monitor-author.sh inspect
```

The script makes a read-only Discord API call to list metadata for the
15 most recent messages from the pinned channel. It prints message IDs,
author IDs, usernames and timestamps, but does not print message content
or the bot token. Find the `author_id` for the message posted by Danny
at the test time. Do not use the Discord bot's `1554225452091048017`
ID unless the bot is actually the message author.

If the API call returns HTTP 403, the bot may lack Read Message History.
Alternatively, in Discord Desktop open User Settings > Advanced >
Developer Mode, right-click the **test message author**, and choose
Copy User ID.

## 2. Pin that author to the quarantine monitor only

With the genuine author's numeric ID, run:

```bash
sudo bash deploy/configure-discord-monitor-author.sh set YOUR_AUTHOR_ID
sudo journalctl -u sktrader-discord-limit-monitor.service -n 35 --no-pager
```

This creates a root-owned systemd override for only
`sktrader-discord-limit-monitor.service`, preserves its quarantine CSV
outside MT5 Common/Files, and restarts only the monitor service. It
refuses to overwrite an existing author override.

A foreign bot or human author typically requires the application bot
to have the **Message Content Intent** enabled in the Discord Developer
Portal under Bot > Privileged Gateway Intents. Otherwise Discord may
reject the subscription with close code 4014, or message content may
be absent. After enabling the intent, restart only the monitor.

## 3. Verify using the next genuine test message

```bash
sudo journalctl -u sktrader-discord-limit-monitor.service -f
```

Expected logs:

```text
Discord Gateway connected; channel 1565527578163552327, author <YOUR_AUTHOR_ID>
Limit signal queued: id=... side=SELL POI=3067.52875
```

The old message **will not** be replayed because the Gateway listener
only processes new `MESSAGE_CREATE` events. Send a **fresh** signal
after reconfiguration, while keeping `EnableNewEntries=false` and
global MT5 Algo Trading disabled.

The monitor is intentionally **not** connected to the EA's actionable
`discord_limit_30m_signals.csv`. Proving Discord detection does not
prove the POI is on the broker's tick grid, that a pending limit order
would be valid at current quotes, or that the broker permits 30-minute
expiration. Those are separate broker-side checks before any trades.
