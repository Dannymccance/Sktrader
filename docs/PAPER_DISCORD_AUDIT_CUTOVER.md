# Five scanner paper trade audit to Discord

Target Discord channel: **1565846495578558526**.

## Deliberate separate events

- SIGNAL RECEIVED: a detected XAUUSD scanner card from the JIFU collector.
- SOURCE DISCORD DELIVERED: the original scanner-specific Discord message was acknowledged by the JIFU watcher and marked delivered in its source outbox. This is distinct from mere signal detection.
- ORDER PENDING: valid signal with complete POI, Invalidation and Exit1;
  no simulated fill has occurred.
- PAPER FILLED: price reached entry using a real bid/ask quote. It is NOT an
  MT5 broker fill confirmation.
- PAPER CLOSED TP1 / SL: simulated exits. Show nominal USD P&L only.
- SIGNAL SKIPPED / ORDER EXPIRED: excluded due to incomplete levels, source
  age, volume risk limits, or expiry.

Each state is held in SQLite and sent to Discord using an outbox. A failed
Discord request leaves the event queued, allowing retries.

## Account and currency

Connected producer: MetaQuotes-Demo account 5058169035, GBP.
The virtual books are USD because XAUUSD quotes are priced in USD and
cash P&L is modelled from (XAUUSD price change) x 100 oz/lot x lots.
**This is not a GBP-account reconciliation** and must not be mixed with MT5
GBP account P&L or historical Vantage REAL USD account statements.
If the broker contract size, reporting currency, or instrument changes,
the replay refuses to run pending verification.

## Deployment safeguards

- Do NOT install or enable the replay service until code review and CI pass.
- First validate clean live ticks with spread <= 1.50 XAUUSD and age <= 30s.
  Closed market ticks are skipped; do not interpret silence as a result.
- Validate first-use source cursor with operator. By default the replay
  checkpoints existing signals and ticks so no stale history becomes live
  simulated fills. Do not delete SQLite databases to restart performance.
- Verify Discord bot has View Channel, Send Messages, Embed Links and
  Read Message History in channel 1565846495578558526.
- The example systemd unit references an example token path. Replace it
  with the existing protected bot token file after checking permissions.
- Do not place broker credentials or bot tokens inside GitHub.

## Outstanding work / QA before production-grade forward testing

1. Quote capture is a rolling MT5 snapshot, NOT guaranteed lossless.
   Add gap-aware append-only broker tick extraction and an explicit sequence
   cursor. Detect gaps and pause simulator when material.
2. Feed identity freshness must be checked alongside actual bid/ask timestamp
   and spreads. Reject abnormal closing-market tick windows.
3. Validate stop distances, margin, stops/freeze levels and broker trade
   specifications. Calibrate real execution latency, fees and slippage.
4. Add a single-message edited portfolio dashboard with independent status,
   realized/unrealized equity, win rate, risk, age/gaps and weekly results.
5. Audit DKCAPITAL legacy account dashboard and broker confirmations
   separately. The existing REAL USD account feed has been stale since
   2026-10-30 20:21:40Z, and old Discord channel IDs may be missing.
   The paper channel cannot repair old GWS broker reporting by itself.
6. Verify each signal notification's Discord response and each paper fill
   event independently. Existing source-specific JIFU Discord cards have
   different delivery paths and acknowledgement statuses.
7. Do not call paper fills 'orders placed' or 'broker confirmed'.
   AUROUX MT5 orders require real broker deal evidence.

## Verification

```bash
cd /opt/sktrader
/opt/sktrader/.venv/bin/python -m unittest discover -s tests -v
```

Do not enable the systemd example without a reviewed follow-up deployment.
