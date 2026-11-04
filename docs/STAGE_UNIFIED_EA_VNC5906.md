# Stage verified Discord unified EA into VNC 5906 MT5 (no trading)

Compilation completed in the isolated MetaEditor on 2026-11-04 with
`Result: 0 errors, 0 warnings`. The verified EX5 is:

```text
/opt/dkcapital-mt5-compiler/artifacts/DiscordLimitUnified_d4385f2a89df.ex5
SHA256 1295fa33add8fbe406500373133781ae0c7f2be12908327985a8e7adefa3ac95
```

The install target is the isolated `dkvnc` Wine environment at
`/var/lib/dkcapital-desktop/.wine-mt5`. Do **not** stage this EX5
into legacy GWS/AUROUX installations or into the MetaEditor compiler.

From VPS root SSH:

```bash
cd /opt/sktrader
git pull --ff-only origin main
sudo bash deploy/stage-unified-ea-vnc5906.sh inspect
sudo bash deploy/stage-unified-ea-vnc5906.sh stage
```

The staging script validates the exact source and EX5 SHA256, confirms
the dedicated localhost VNC 5906 service, finds exactly one MT5 data folder in either the isolated Wine user's
Roaming profile or the verified portable-style installation folder
`/var/lib/dkcapital-desktop/.wine-mt5/drive_c/Program Files/MetaTrader 5/MQL5/Experts`,
and stages only the `DiscordLimitUnified.ex5` binary. The portable
folder is accepted only when the same installation also has
`Config`, `Bases` and `logs` directories. If zero or multiple
candidate folders exist, it exits safely. In that case choose **File > Open Data Folder** in the running MT5, identify the correct folder, and investigate before staging.
It refuses to replace a different file with the same name.

In the running VNC MT5 terminal: Navigator > Expert Advisors > Refresh.
Attach a single `DiscordLimitUnified` instance to an XAUUSD chart
**with EnableNewEntries=false**. First inspect Expert logs, connected
account mode, broker symbol specifications and read-only telemetry.

**Important:** Copying the EX5 does not attach it or enable trades, and
this staging step does not start the Discord listener. The listener
destination must subsequently be configured to the new Wine prefix's
`Terminal/Common/Files` folder before signal-driven trading works.
The previously supplied service example still points to a retired demo
prefix. Keep new entries disabled until the Discord feed, order sizing,
broker support for timed pending orders, deduplication and account
margin mode have been inspected. Hedging permits independent overlapping
positions. Netting/exchange accounts are telemetry-only in v2.01.

No live broker changes or other MT5/JIFU services are made by staging.
