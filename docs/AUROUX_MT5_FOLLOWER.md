# AUROUX MT5 Follower

Independent AUROUX XAUUSD EA, no modification to GWS EA.

## Input and execution

- Source: Sktrader's existing durable `scanner_events` SQLite database. Only records where `scanner=AUROUX` and `symbol=XAUUSD` are exported. This is the source for AUROUX channel 1565508564775075981, subject to confirming the collector's live channel mapping.
- Levels: **Point of Interest** = entry, **Invalidation** = SL, **Exit1** = TP1; all remaining exits ignored.
- **4% of account balance** nominal risk, using `OrderCalcProfit` against POI and SL on broker's instrument. Volume **rounds upward** to broker step and can exceed 4% actual risk, sometimes materially for tiny accounts. Broker minimums outside calculated range are skipped. Commissions, slippage and gap fills can also raise actual loss.
- Immediate entry if within `EntryTolerancePoints` broker points of POI, otherwise pending limit or stop at POI. Pending orders are GTC and **require manual management** if the signal expires. No time, spread or news filters. Orders may be rejected by broker distance restrictions.
- One active AUROUX position or pending order per **MagicNumber/symbol** at a time. Other new signals while one is active are skipped.
- Durable MT5 terminal-global cursor advances only after accepted order or a deterministically rejected/skipped event. Broker orders use `AUR#<event id>` comments. Duplicate detection also checks orders and history. Use a distinct MagicNumber for each strategy/account.
- On first EA installation, existing events are skipped by default to prevent executing historical signals. Do not clear MT5 global variables when restarting. A second EA instance with the same MagicNumber/account must not run concurrently.

## Steps

1. On the MT5 *installation that will execute trades*, use **File > Open Data Folder**. The file bridge uses the shared common path shown by `TerminalInfoString(TERMINAL_COMMONDATA_PATH)` in MetaEditor/MQL5, under `Files/auroux_signals.csv`. This may be a Wine-based Windows profile and is not necessarily `/opt/sktrader`.
2. Copy `mt5/AurouxFollower.mq5` into the MT5 data directory `MQL5/Experts`, compile it in MetaEditor, and confirm the compiler reports zero errors.
3. Run the exporter from this repository, using a destination visible to that MT5 terminal:
   ```bash
   cd /opt/sktrader
   .venv/bin/python -m sktrader.auroux_bridge \
     --database /var/lib/dkcapital-mt5-3/.local/share/sktrader/signals.db \
     --output '/ACTUAL/MT5/COMMON/Files/auroux_signals.csv' --follow
   ```
   The exporter account needs read access to SQLite and write access to the MT5 common files directory. On separate machines, a file transport is required before this can function.
4. Add the EA to the broker's XAUUSD chart, enable **Algo Trading**. Verify the symbol/lot step and terminal-global checkpoint in Experts logs. The EA trades on **whatever MT5 account it is attached to**, so take care with REAL accounts.
5. Check Experts logs for `AUROUX <id> submitted` and confirm TP1/SL on the order. Confirm no duplicate occurs after restarting the EA.

The bridge does not read Discord as a separate source and does not generate extra browser sessions. The Discord channel ID is not used for trade submission. No MT5 EA deployment, compilation, or live trading activation is performed by this pull request.
