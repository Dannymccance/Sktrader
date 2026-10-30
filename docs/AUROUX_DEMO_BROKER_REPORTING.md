# AUROUX Demo broker-confirmed reporting (stage 4)

## Why a new broker feed

The legacy DKCAPITAL Account 3 gateway and Discord dashboards are pinned
to an archived **Vantage REAL USD** account. The current MT5 terminal is
logged into **MetaQuotes-Demo GBP**, login 5058169035. Reusing a REAL
account snapshot or changing its identity to DEMO would falsify the account
history and must never be done.

## Actual versus simulated evidence

- The JIFU scanner event is **SIGNAL DETECTED**; source-channel Discord
  delivery must have its own successful Discord message acknowledgement.
- A paper trade fill is always **SIMULATED** and cannot be used as proof
  of execution.
- A real AUROUX demo order fill or closure must have an **MT5 broker deal
  ticket**, order ticket, account identity, magic 2601030, instrument,
  volume, execution price, deal entry type and broker timestamp.
- Actual GBP demo balance/equity/margin and position P&L are sourced
  from terminal AccountInfo/PositionGet fields only.
- No dealer order placement or account changes are possible from this
  read-only exporter/reporter.

## Components

1. `mt5/SktraderBrokerAccountPublisher.mq5` writes three atomic MT5
   FILE_COMMON CSV exports on a 5-second timer: account, positions and
   last seven days of broker deals.
2. `sktrader/broker_reporting.py` ingests strictly verified DEMO/GBP
   identity and persists immutable deal tickets. The first ingest baselines
   historical deals and posts no backfilled broker confirmations.
3. New broker deals with the exact AUROUX magic number are sent to
   configurable Discord confirmation / result channels and acknowledged
   by message IDs.
4. A single broker-verified demo dashboard message is edited every
   ~30 seconds. GBP amounts remain GBP and are never conflated with
   the five USD-denominated virtual portfolios.

## Commissioning

- Pull only after CI passes and PR is merged.
- Use the already available isolated MetaEditor compiler to compile
  `SktraderBrokerAccountPublisher.mq5`. GitHub Python CI cannot
  validate MQL5 compilation. Confirm 0 errors.
- Install the resulting EX5 into the **existing DEMO MT5 terminal**.
  Attach it to an additional XAUUSD chart. Leave AUROUX EA and market-data
  EA attached to their respective charts.
- Verify the new `sktrader_demo_account.csv`,
  `sktrader_demo_positions.csv`, and `sktrader_demo_deals.csv`
  in the same FILE_COMMON directory as the existing market exports.
- Confirm account 5058169035, DEMO/GBP, MetaQuotes-Demo in the CSV.
- Configure three actual accessible Discord channel IDs in the .env:
  `AUROUX_DEMO_DASHBOARD_CHANNEL_ID`,
  `AUROUX_DEMO_CONFIRMATIONS_CHANNEL_ID`, and
  `AUROUX_DEMO_RESULTS_CHANNEL_ID`. The bot needs View Channel,
  Send Messages, Embed Links, Read Message History, Edit Own Messages.
- The example token path is not assumed to exist. Use the bot token's
  currently configured root-readable, owner-only file and permissions.
- Install the example systemd service as a new name only after verifying
  login, all channel IDs, journal logging and first-run baseline.
- Check an authentic demo broker deal *after* service activation and its
  matching Discord message ID to validate order confirmations end-to-end.

## Still outstanding for full legacy dashboard restoration

- Independently repair inaccessible old DKCAPITAL Discord channel mappings
  and reconnect the live/REAL account to fresh authenticated telemetry only
  if that REAL MT5 account is again actively monitored.
- Restore heartbeat/health alerts, weekly record scheduling and pip-history
  reconciliation to the appropriate new DEMO profile. The old live account's
  reporting must remain historically labeled stale if not connected.
- Reconcile broker commission, deal exit attribution, partial fills and
  swaps against MT5 history using sample live DEMO positions.
- Validate actual Discord permissions and reception on the VPS.
  Neither GitHub CI nor source code alone proves message delivery.

```bash
cd /opt/sktrader
/opt/sktrader/.venv/bin/python -m unittest discover -s tests -v
```
