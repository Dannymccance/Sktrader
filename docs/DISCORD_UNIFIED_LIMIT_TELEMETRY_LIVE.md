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
- Account-agnostic attachment and telemetry across DEMO, CONTEST and REAL logins and broker servers. Independent overlapping positions require a hedging account. On netting/exchange accounts, trading entries stay blocked and telemetry still runs.

## Portable account identity and trading arming

`DiscordLimitUnified.mq5` version 2.01 has **no login, broker-server or
DEMO/CONTEST/REAL whitelist**. It can attach on a connected account of any
trade mode. The EA dynamically reads `ACCOUNT_BALANCE`, `ACCOUNT_CURRENCY`
and `OrderCalcProfit` on the broker's exact XAUUSD contract to calculate
each 4%-of-balance order size. The broker login and server are used **only**
to namespace persistent Discord-message deduplication, not to authorize
accounts. Trading remains disabled by default: `EnableNewEntries=false`.

**Important netting limitation:** The strategy promises independent,
overlapping 4%-risk XAUUSD positions, each with its own broker-native $7.50
SL and $15 TP. MT5 *hedging* accounts support this. *Netting/exchange*
accounts consolidate same-symbol positions and cannot provide independent
broker-native SL/TP for each signal. The unified EA therefore initializes,
publishes broker telemetry and logs warnings on such accounts, but blocks
new trading entries there. Do not remove that safeguard without developing
and separately validating position-level netting management.

No account's broker credentials or preconfigured account number need to be
embedded into EA source or inputs. The correct terminal data folder and
Discord listener routing **still** have to be verified when switching
between terminal installations to avoid sending signals into the wrong
terminal.

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

## Account-agnostic installation

1. Identify the actual MT5 terminal installation and `Terminal Data Folder`
   to receive the unified EA. The EA itself does not require an account ID.
2. Compile and verify the unified EX5 before considering installation.
3. Stage the EX5 in the selected terminal's `MQL5/Experts` directory without
   replacing GWS or AUROUX binaries.
4. Configure the Discord listener output in the same terminal's
   `Common/Files`. The service example still targets an older DEMO folder
   and **must** be updated for the selected terminal.
5. Attach exactly one unified EA to a broker XAUUSD chart and keep
   `EnableNewEntries=false` initially. Verify the EA reports current login,
   mode, symbol, telemetry, deduplication and order parameters.
6. For trading, verify the broker's XAUUSD contract and hedging margin mode,
   volume increments, expiration support and loss sizing. Netting accounts
   are telemetry-only to preserve the independent-stop strategy.
7. Enable new entries deliberately only after testing the connected terminal
   and verifying real broker-native order tickets, stop levels and expiry.
8. Preserve all pre-existing GWS and AUROUX instances and their state.

Do not interpret a GitHub merge, file copy or a successful Python
unit test as proof of MQL5 compilation or live execution.
