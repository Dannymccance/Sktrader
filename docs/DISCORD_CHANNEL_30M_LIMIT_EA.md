# Discord channel 1565527578163552327 -> fixed-risk XAUUSD limit EA

Independent system, not a modification to GWS, AUROUX, or the existing scanner collector.

## Exact strategy

- Source: new Discord MESSAGE_CREATE event in channel **1565527578163552327**, with
  the trusted bot author ID (defaults to the current bot's ID). No polling
  old Discord messages, no replay of old chat history.
- Accept only a valid XAUUSD BUY/SELL signal with exactly one numeric **Point
  of Interest**. The source's Invalidation and Exit1 are intentionally ignored.
- **BUY** -> BUY_LIMIT at POI; SL = POI - 10.00; TP = POI + 10.00.
- **SELL** -> SELL_LIMIT at POI; SL = POI + 10.00; TP = POI - 10.00.
- Broker-specified pending order expiry = 30 minutes from the original
  Discord message creation time, not from when the EA happens to read it.
- Per-order risk budget = 4% of current MT5 account balance, evaluated using
  OrderCalcProfit on the broker's actual XAUUSD contract and account currency.
  Round lots **down** to the broker volume step. If the minimum trade would
  exceed the 4% budget, do not place it.
- No market entries, stop entries, break even, trailing SL, partial exits, or
  targets beyond TP1. Individual orders can overlap; **there is no aggregate
  portfolio-risk cap**. Five independent 4% orders could put ~20% at risk.
- Strictly demo-only on MetaQuotes-Demo GBP account login 5058169035 until
  explicitly reconfigured and reviewed. Separate MagicNumber 26110430.
- Broker must support ORDER_TIME_SPECIFIED. No GTC substitute is used.
- When a BUY_LIMIT POI is above the current ask, or SELL_LIMIT POI below
  current bid, the order is not placeable as a limit. The EA waits for a
  valid limit position until the same original 30-minute expiry. It never
  changes order type or sends a market order.

## Latency path

1. Real-time Discord Gateway WebSocket (no REST message polling).
2. On receipt, append to SQLite and atomically replace a five-column CSV in
   the MT5 Terminal/Common/Files folder. CSV: `message_id;BUY/SELL;POI;created_unix_s;expires_unix_s`.
3. MT5 EA reads this file at 50 millisecond timer intervals and submits a
   limit order immediately when valid. Order comments are `DCL#<Discord message id>`.
   Handled IDs are persisted separately to prevent duplicate submissions.
4. Expiry is broker-side. The EA does not need to remain running to cancel
   an unfilled pending order at the configured expiry.

Discord and broker network latency, terminal scheduling, broker tick-size,
minimum lot size and broker rejection rules prevent any guaranteed fill or
guaranteed 50ms end-to-end execution.

## Prepare listener (does not submit broker orders)

Use the existing bot token at the existing configured secure file location.
The bot must have View Channel permissions in channel 1565527578163552327,
and the Discord Developer Portal must enable the privileged **Message
Content Intent** if required to receive signal fields. GUILD_MESSAGES is
requested too. The listener defaults to messages authored by that same bot.
For a different trusted source bot, set `--author-id` to its actual Discord
user ID in the service ExecStart line, not to a role or channel ID. Do not
disable author checks.

```bash
cd /opt/sktrader
git pull --ff-only origin main
sudo -u dkmt5c /opt/sktrader/.venv/bin/pip install -r /opt/sktrader/requirements.txt
sudo cp /opt/sktrader/deploy/sktrader-discord-limit-30m.service.example /etc/systemd/system/sktrader-discord-limit-30m.service
sudo systemctl daemon-reload
sudo systemctl enable --now sktrader-discord-limit-30m
sudo journalctl -u sktrader-discord-limit-30m -n 30 --no-pager
```

This service observes one channel and writes files, not broker orders.

## Install the NEW EA (DEMO MT5 only)

Compile `mt5/DiscordChannelLimit30m.mq5` using the **separate isolated
MetaEditor** for VNC 5903, then verify zero compilation errors and a fresh
`DiscordChannelLimit30m.ex5`. This compiler previously failed to generate
EX5 files under Wine and must be repaired/verified before deployment.
Installing source alone does not execute an EA.

Copy the verified EX5 into the approved DEMO MT5 terminal's `MQL5/Experts`,
attach **one instance** to that terminal's XAUUSD (or XAUUSD+) chart, and
enable Algo Trading. The EA refuses any account other than DEMO login
5058169035. It initially marks all preexisting CSV rows handled, so attaching
it cannot replay stale signals. Subsequent new messages only are eligible.

To check output:

```bash
sudo systemctl is-active sktrader-discord-limit-30m
sudo journalctl -u sktrader-discord-limit-30m --since '10 minutes ago' --no-pager
sudo tail -10 '/var/lib/dkcapital-mt5-3/.wine/drive_c/users/dkmt5c/AppData/Roaming/MetaQuotes/Terminal/Common/Files/discord_limit_30m_signals.csv'
```

A live test requires a new genuine signal, the EA Experts logs and a
broker-confirmed order ticket. Do not post a synthetic live signal just to
test execution with Algo Trading enabled. Historical signal #60 is not
replayed by this system.

**Risk limitations:** 4% is a nominal entry-to-stop budget, excluding
commissions, swap, slippage, and gaps. Real losses can exceed 4%. Pending
orders may fail to place or never fill. Verify on demo before any live use.
