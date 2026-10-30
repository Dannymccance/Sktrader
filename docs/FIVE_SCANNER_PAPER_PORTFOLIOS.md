# Five JIFU scanner paper portfolios (stage 1)

**Status:** simulation engine and tests only. NO automated live ingestion or
broker publisher is deployed by this PR. Do not describe these paper portfolios
as forward-testing live until the authenticated market-data receiver is built,
tested and deployed.

## Immutable strategy configuration

- Scanners: SNIPER PRO, NEXUS, HARMONICS, PRIME, WOLF X.
- Each scanner starts with **$1,000 USD** and has a separate persistent ledger.
- Target risk is **4% of that portfolio's current cash balance per entry**.
- XAUUSD only; TP1 only; provider Invalidation as SL. Missing or ambiguous
  POI, stop or Exit1: **SKIPPED**. No guessed levels.
- No cross-scanner pooling, no portfolio borrowing, and no live orders.
- One open paper position per scanner. Pending events are distinct by
  Sktrader scanner_events.id and are not replayed twice after restart.
- A bounded 180-second validity window is required from an independently
  established signal expiration. Never extend it because Discord delivery
  was late. The publisher's display-age approximation is not a precise
  publication timestamp.
- Position size rounds DOWN to broker volume step. Below-minimum lots
  are skipped rather than exceeding the nominal risk allowance.
- Quotes must be valid time-ordered bid/ask observations; OHLC alone cannot
  establish entry/SL/TP ordering. M1 candles are for charting and gap auditing.
- SL gaps are conservatively filled at the worse observed quote; TP assumes
  the target price, never a more favorable exit.
- Results are simulated, not broker-confirmed. The current model does not
  model order queueing, network latency, negative fill on entry, swap,
  or all commission/contract variations. Do not present it as broker-equivalent.

## Already present in Sktrader

The collector saves source-specific observations in
`scanner_events(id, observed_at, payload)` within its SQLite database.
`sktrader.multi_scanners` normalizes scanner aliases and preserves variable
fields. Some groups (e.g. PRIME) can send signals without a stop loss.
Those are explicitly skipped by this first model.

## Next required PR before automatic forward testing

1. Add a **read-only MT5 publisher** on a separate chart, independent from
   AUROUX's executing EA. Export account identity, contract specification,
   completed XAUUSD M1 candles, and ordered bid/ask ticks. Use a distinct
   demo account profile, never the archived Account 3 REAL USD identity.
2. Deliver quotes to a locally authenticated VPS receiver with stable quote
   IDs and UTC timestamps. Keep channel private; never expose a market-data
   ingestion endpoint directly to the internet.
3. Persist quotes in append-only storage with gap flags and backfill.
   Detect terminal disconnects, stale market data and late source signals.
4. Build a replay coordinator that consumes only NEW qualifying
   scanner_events records, calculates conservative signal expiry, and
   applies ticks in stable order with transactional checkpoints.
5. Reconcile broker contract-size/tick-value/lot-step metadata; validate
   USD calculations against MT5 OrderCalcProfit samples before activation.
6. Add broker execution cost calibration, bid/ask historical tick continuity,
   commissions, spread, slippage and overnight rollover rules.
7. Connect read-only dashboard and Discord reporting, clearly marking
   **SIMULATED** vs **BROKER VERIFIED**, with separate immutable account IDs.
8. Test gap/crossed-candle scenarios, duplicate messages, restart safety,
   180-second expiry, simultaneous opposite signals and simulator continuity.

## Running unit tests

```bash
python3 -m unittest tests.test_paper_portfolios -v
python3 -m unittest discover -s tests -v
```

This PR adds NO systemd services, no MT5 writes, no Discord sends and no
changes to AUROUX, DK Capital GWS trading, or existing telemetry.
