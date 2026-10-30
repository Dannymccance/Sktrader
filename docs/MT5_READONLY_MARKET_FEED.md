# Read-only MT5 XAUUSD market publisher (stage 2)

This PR adds the **source and ingestion logic only**. It does not install a
service, attach an EA, restart a terminal, or enable paper execution.

## Design

- `mt5/SktraderMarketPublisher.mq5` is intended for a **separate XAUUSD
  demo chart**. It contains no CTrade/OrderSend/trade execution code.
- `OnTimer` exports bounded rolling bid/ask ticks, completed M1 candles and
  account identity/contract metadata into MT5 Terminal Common Files.
- It refuses non-demo accounts by default. That is deliberate: the old
  Account 3 REAL/USD history must not be overwritten or conflated with the
  newer AUROUX demo account.
- `python -m sktrader.market_feed` validates login, mode DEMO, currency USD,
  fresh identity, quote geometry and OHLC, and persists each tick and M1 bar
  in a separate SQLite database. Repeating exports is idempotent.
- The market publisher emits **no account balance, open-position or broker
  deal-history telemetry yet**. That will need a separate schema and safe
  consumer before original Discord reporting can be restored.
- The publisher has not been compiled under Wine/MetaEditor or broker-tested.
  Treat it as a code candidate, not a deployable verified EX5.
- DO NOT feed the simulator from M1 candles: intra-bar path ambiguity makes
  SL/TP sequencing unreliable. Consume ordered bid/ask ticks; mark gaps.
- Live quote ingestion is independent from `sktrader.service` (JIFU collector),
  `sktrader-auroux-bridge.service`, and AUROUX execution.
- This code has **no public network listener** and does not need a new port.

## After CI and code review, deployment gate

1. Verify which actual account is logged into MT5 and obtain the **demo login
   from the terminal itself**. Do not reuse historical REAL login 36193223.
2. Compile `SktraderMarketPublisher.mq5` using the isolated MT5 compiler.
   Confirm 0 errors and version/hash. Install the EX5 into the same demo
   terminal without replacing or detaching `AurouxFollower`.
3. Attach publisher to a separate XAUUSD chart, with demo-only guard on.
   Inspect the Experts tab. Tick exports may not advance when market closed.
4. Verify the three CSV files exist in the same common files directory as
   `auroux_signals.csv`. Do not edit the signal CSV.
5. Run an initial receiver probe, substituting the verified demo login:

```bash
COMMON='/var/lib/dkcapital-mt5-3/.wine/drive_c/users/dkmt5c/AppData/Roaming/MetaQuotes/Terminal/Common/Files'
sudo -u dkmt5c /opt/sktrader/.venv/bin/python -m sktrader.market_feed \
  --common "$COMMON" \
  --database /var/lib/dkcapital-mt5-3/.local/share/sktrader/market_data.db \
  --expected-login DEMO_LOGIN_FROM_MT5
```

6. Run against live demo quotes after market reopening and inspect
   correctness, throughput, deduplication, time ordering and missing ticks.
7. Separate follow-up PR: reliable account/deal telemetry, safe signal
   replay coordinator, gap handling and Discord simulated portfolio reports.

Never claim live forward testing is running solely from this PR.
