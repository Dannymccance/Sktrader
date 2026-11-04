# Unified Discord XAUUSD limit-order trader and MT5 telemetry

One MT5 EA, DiscordLimitUnified.mq5, replaces separate DiscordChannelLimit30m and broker telemetry publisher on a given target terminal. A lightweight VPS Discord Gateway service remains necessary to receive messages.

## Trading rules

- Discord channel 1565527578163552327 with exact trusted author, XAUUSD numeric POI.
- BUY_LIMIT or SELL_LIMIT at POI only, never market or stop orders.
- $7.50 adverse-price SL, $15 favourable-price TP, one 100% target.
- Broker-specified expiry 30 minutes after original Discord message creation.
- 4% of current balance risk per order using OrderCalcProfit and rounding volume DOWN.
- No overlap restrictions except deduplication of identical Discord message IDs. NO aggregate risk cap.
- 50ms signal-file polling. No breakeven or trailing.
- Requires a hedging account for independent overlapping positions.

## Account identity and arming

Input defaults: RequiredTradeMode is REAL, ExpectedAccountLogin is 0,
ExpectedAccountServer is empty, and EnableNewEntries is false.
The EA refuses to initialize unless the actual connected terminal account
matches the explicitly supplied login, server, trade mode and hedging status.
New entries remain disabled until EnableNewEntries is deliberately set true.

Earlier VPS evidence identified dkcapital-mt5-3 as MetaQuotes-Demo GBP,
not Vantage REAL. Do NOT guess the current REAL login from archived
Account 3 snapshots or copy a binary to the demo directory expecting
it to execute on REAL.

If the user intends the current demo rather than REAL, the required mode
must explicitly be set to DEMO with the correct login and server.

## Telemetry inside the same EA

- Account state, position snapshots and broker deals every five seconds,
  plus an expedited account/deals refresh after a trade transaction.
- Separate filenames: discord_limit_account.csv,
  discord_limit_positions.csv, discord_limit_deals.csv.
- Optional XAUUSD quote, closed M1 candle, and instrument identity snapshots:
  discord_limit_xau_quotes.csv, discord_limit_xau_m1.csv,
  discord_limit_xau_identity.csv. OFF by default for faster order handling.
- Order polling occurs BEFORE any scheduled telemetry work.
- AUROUX's existing broker_reporting.py consumer is DEMO-specific and
  must NOT be pointed at REAL-account telemetry. New reporting consumption
  must separately verify account identity.

Five overlapping 4%-risk positions imply approximately 20% nominal
balance-at-risk before transaction costs and any correlated slippage.

## Compile with MetaEditor VNC 5903

After merging this feature:

    cd /opt/sktrader
    git pull --ff-only origin main
    sudo bash deploy/compile-discord-limit-unified.sh

This script uses the isolated dkcapital-mt5-compiler-vnc container,
verifies the EX5 and zero-error compiler log, and creates a
compiler-only artifact under /opt/dkcapital-mt5-compiler/artifacts.
It does NOT install, attach or activate any trading EA. Previously,
the compiler failed to produce EX5/log under Wine and this may still
be a blocker. GitHub CI cannot compile MQL5.

## Verified REAL cutover

1. Read the REAL terminal's actual broker login, server, mode and
   Terminal Data Folder. Confirm it is not the MetaQuotes demo prefix.
2. Compile and verify the unified EX5 before considering installation.
3. Stage the EX5 in that verified REAL Experts directory without
   replacing GWS binaries.
4. Configure the Discord listener output in the verified REAL
   terminal Common/Files. The service example still targets DEMO.
   A listener writing in the wrong Wine prefix cannot feed that EA.
5. Attach ONE unified EA to an XAUUSD chart. Never simultaneously
   attach the old DiscordChannelLimit30m EA for the same channel.
6. Keep new entries disabled and verify telemetry, signal source,
   account guard, dedupe and expected order levels.
7. Explicitly enable entries only after a reviewed live-account
   deployment, and verify future genuine orders against broker tickets.
8. Preserve existing GWS and AUROUX instances and their state.

Do not interpret a GitHub merge, file copy or a successful Python
unit test as proof of MQL5 compilation or live execution.
