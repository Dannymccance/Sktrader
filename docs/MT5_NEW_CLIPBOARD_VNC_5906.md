# Install MetaTrader 5 inside the new clipboard-enabled VNC (port 5906)

This setup is **only** for the unprivileged `dkvnc` XFCE desktop at
`/var/lib/dkcapital-desktop`. It is separate from the 5903 MetaEditor
compiler container and from all existing GWS/AUROUX MT5 Wine profiles.

MetaQuotes officially documents running MetaTrader 5 under Wine on Linux.
Instead of executing their system-changing Linux script, this setup stages
the official Windows installer in an isolated user-owned Wine prefix.

## Prepare using your root SSH PowerShell tab

```bash
cd /opt/sktrader
git pull --ff-only origin main
sudo bash deploy/install-mt5-vnc5906.sh prepare
```

This validates the new localhost-only VNC on port 5906 and the compiler on
5903, checks free space, installs the Ubuntu Wine runtime if necessary,
downloads the official MetaQuotes `mt5setup.exe` and validates its
Windows executable signature, and creates launchers.

The script does not start trading, log into a broker, attach an EA, change
old MT5 account data, or restart any MT5 service. Wine host packages are
system-wide, so installing them could affect future use of other host Wine
processes. The compiler runs in Docker and is not modified.

## Launch GUI installation in your new VNC desktop

Keep your Windows SSH tunnel open:

```powershell
ssh -N -L 5906:127.0.0.1:5906 root@31.187.72.71
```

Connect TigerVNC to `127.0.0.1::5906`. In the XFCE terminal running as
`dkvnc`, enter:

```bash
~/bin/install-mt5
```

Click through the MetaTrader installation wizard. If Wine asks for Gecko
or Mono components, accept only the expected Wine dialogs.

Once installation is complete, start or reopen the platform with:

```bash
~/bin/mt5
```

There are also two desktop launchers, though XFCE might prompt whether
to trust them initially. Terminal commands are the most reliable option.

## Broker login and unified EA

MT5 should open without an account attached. Sign in only inside the
MT5 login GUI using the real or demo account and **exact broker server**
shown by your broker. Never paste credentials into ChatGPT or Discord.

Before putting the new unified EA on this terminal, verify:
- Account login and server for selecting the correct terminal data folder
  and checking telemetry routing, not as hardcoded EA authorizations.
- Account balance currency, XAUUSD contract specification and margin mode.
  Netting/exchange accounts support telemetry only until an independently
  risk-validated netting position manager exists.
- Correct XAUUSD symbol and that the terminal is connected.
- The new terminal's distinct data folder under
  `/var/lib/dkcapital-desktop/.wine-mt5`.
- A fresh, successful MetaEditor zero-error compilation of
  `DiscordLimitUnified.mq5`.
- Existing Discord listener is redirected to the new prefix's Common Files
  only after reviewing and testing the account-specific cutover.

Unified EA v2.01 can initialize on any MT5 login, broker server and
DEMO/CONTEST/REAL mode without input-specific whitelists.
It defaults to `EnableNewEntries=false`. No trading will be enabled by
this installation. Independent overlapping SL/TP entries remain limited
to hedging accounts.

For diagnostics, in root SSH:

```bash
sudo bash /opt/sktrader/deploy/install-mt5-vnc5906.sh status
```
