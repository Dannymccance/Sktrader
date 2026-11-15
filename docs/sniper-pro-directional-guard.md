# Sniper Pro directional guard EA (separate demo candidate)

Source: `mt5/DiscordLimitDirectionalGuard.mq5`

This is a **separate copy** of `mt5/DiscordLimitUnified.mq5`. No changes are made to the original EA, Discord bridge, existing services, compiler VNC or account configuration.

## Rules retained exactly from existing EA

- XAUUSD (including XAUUSD-suffixed symbols) on a hedging MT5 account.
- Read existing `discord_limit_30m_signals.csv` in Terminal Common/Files; same five semicolon-delimited fields: `message_id;BUY|SELL;poi;issued_utc;expires_utc`.
- Only pending BUY LIMIT / SELL LIMIT at the signal POI. No market-order fallback.
- 30 minutes from the Discord signal creation time to pending-order expiry.
- 4% of **account balance** at risk for **each** trade, calculated from entry to SL.
- BUY SL = entry - $7.50; SELL SL = entry + $7.50.
- BUY TP = entry + $15; SELL TP = entry - $15.
- Independent overlapping signals allowed; **no aggregate portfolio risk cap**.
- No break-even, no trailing stop, no partial take profits.
- Initial attachment defaults to `EnableNewEntries=false` and checkpoints messages already present in the feed.

## New conditional directional circuit breaker

Defaults:

| Input | Value |
|---|---|
| EnableDirectionalGuard | true |
| GuardLogOnly | false |
| GuardCooldownSeconds | 7200 |
| GuardTrendSeconds | 7200 |
| GuardAdverseUSD | 5.0 |
| GuardMaxReferenceAgeSeconds | 90 |
| GuardMaxQuoteAgeSeconds | 60 |
| EnableNewEntries | false |
| MagicNumber | 26111520 |

When the EA is about to **place a new eligible limit order**, it:

1. Reads broker deal history from the previous 7200 seconds (broker server time).
2. Finds actual broker `DEAL_REASON_SL` closing deals belonging to this EA's magic number, verifying opening position history as a fallback if the broker reports zero magic on the closing deal.
3. For a BUY, looks for a recent stopped-out BUY. For a SELL, looks for a recent stopped-out SELL. The opposite direction never starts a block.
4. If there is no matching SL, permits the signal normally.
5. If there is an SL, compares current **BID** with the **open of the M1 candle around two hours earlier**, aligned with broker server time. Rejects a BUY when change <= -$5; rejects a SELL when change >= +$5.
6. If market history or the quote is stale or unavailable while a matching SL exists, **defers order placement** rather than silently passing the guard. The original 30-minute message deadline still applies.
7. If blocked, logs the reason and makes the Discord message ID permanently handled, avoiding late entry after the cooldown or trend condition disappears. Already-placed pending orders and open positions remain untouched.
8. The restriction automatically stops applying after the two-hour SL window has elapsed, or when adverse price movement no longer meets the threshold.

This is deliberately **not** an unconditional two-hour pause. Nor does it cancel accepted pending orders.

### Logging

- MT5 Experts log: `DIRECTION_GUARD BLOCKED` or `DIRECTION_GUARD WOULD_BLOCK`.
- Common/Files: `discord_limit_guard_decisions.csv`, including message ID, UTC evaluation time, BUY/SELL, most recent SL server time, reference price, current BID, two-hour change and decision.
- GuardLogOnly=true records what would be blocked **without applying the block**, for a non-disruptive observation test. It still requires `EnableNewEntries=true` to reach the order gate.
- Telemetry export filenames start with `discord_guard_` so they do not overwrite original EA exports.
- Handled IDs are isolated by login, broker server, magic and `discord_limit_guard_handled.txt`. Stop-loss history is isolated by magic.

## Critical caveats

1. **The five-field bridge CSV does not contain signal timeframe.** As of this change, the repository's `sktrader/discord_limit_bridge.py` regex recognises 1M, 5M, 15M, 30M, 1H and 4H without an explicit 5M/15M/30M allowlist inside `extract_signal`. The new EA cannot independently verify timeframe from that feed. Confirm the currently deployed upstream filtering before enabling trading. This PR intentionally avoids modifying the existing running bridge.
2. **Do not arm both the original and guard EA on the same account and signal feed.** They have distinct magic IDs and will otherwise submit duplicate trades. Use an isolated demo terminal/account or disable original entries.
3. The rule does **not** cap correlated/overlapping risk, prevent all losing streaks, or guarantee lower drawdown. Eight simultaneous positions at 4% each still represent approximately 32% of balance at initial stops, ignoring slippage and interactions.
4. No SL protection transfers automatically from another EA's magic number. A new magic starts a fresh SL tracking history. The EA reconstructs its **own** recent stop-losses from MT5 broker history on restart.
5. The price comparison uses an M1 candle **open**, not a historical tick exactly 7200 seconds ago. This is an intentional low-overhead approximation with a freshness check, and it differs slightly from a tick-by-tick research replay.
6. `EnableNewEntries` defaults to **false**. Do not assume the EA is executing orders after merely attaching it.
7. This branch has not been compiled in MetaEditor or exercised on a real broker account in this environment. Treat it as a demo candidate pending successful MT5 compilation and scenario tests.

## VPS handoff (safe; no service restart)

Fetch just the new EA file without checking out or resetting the VPS repo:

```bash
cd /opt/sktrader
git fetch origin feature/sniper-pro-directional-guard-20261115
git show origin/feature/sniper-pro-directional-guard-20261115:mt5/DiscordLimitDirectionalGuard.mq5 > /tmp/DiscordLimitDirectionalGuard.mq5
wc -l /tmp/DiscordLimitDirectionalGuard.mq5
```

Open the file in the **existing, separate compiler MetaEditor** (under the existing compiler VNC user), save it as `MQL5/Experts/DiscordLimitDirectionalGuard.mq5`, compile and confirm **0 errors**. Do not overwrite `DiscordLimitUnified.mq5` or its EX5 file. Do not reset the git checkout, overwrite any `Common/Files` CSV, or restart the existing bot/services/compiler VNC.

Test on a separate MT5 demo/account or with original EA entries disabled. Check XAUUSD symbol mapping, quote/broker history availability, expected signal selection, journal and risk volume. The initial EA is disarmed by default. Only after those checks, enable Algo Trading and `EnableNewEntries=true` for the **test** EA.

## Acceptance checks in MetaTrader

1. No recent matching SL, falling market, new BUY: **allowed**.
2. Recent EA-owned BUY SL, two-hour movement -$5.01, new BUY: **blocked**, id marked handled, audit row recorded.
3. Same condition, new SELL: **allowed** unless a recent SELL SL and +$5 adverse move also exist.
4. Recent BUY SL, two-hour movement -$4.99: BUY **allowed**.
5. Recent SELL SL, two-hour movement +$5.01, new SELL: **blocked**.
6. SL older than 7200 seconds: no longer blocks new signals.
7. Broker has an SL on a different magic number: no block.
8. No M1 history or stale weekend reference after a relevant SL: **no order placed** while the guard cannot evaluate; recovery rechecks until message deadline.
9. MT5 restart during cooldown: previous EA-owned SL still recognised from broker history.
10. Existing open positions and already-placed pending limit orders remain intact.
11. `GuardLogOnly=true`: logs `WOULD_BLOCK`, keeps normal original execution eligibility.
12. Existing EA remains unchanged and functional throughout.

Keep this branch separate from `main` until MetaEditor compilation and forward-demo verification are complete.
