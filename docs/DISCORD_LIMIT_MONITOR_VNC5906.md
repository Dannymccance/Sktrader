# Discord Gateway listener for unified EA on VNC 5906

The unified EA has been staged and attached to the new isolated MT5
terminal. MT5 writes `discord_limit_account.csv` successfully, but
`sktrader-discord-limit-30m.service` is inactive and there is no
`discord_limit_30m_signals.csv` in the new terminal's Common/Files.

**Important:** A screenshot of EA settings showed `EnableNewEntries=true`.
Turn it OFF and keep global MT5 Algo Trading OFF while checking the source.
The Discord listener is not yet connected and should not be pointed at
MT5 until the source channel and trusted message author have been verified.

## Safely start a non-trading listener

From the VPS SSH terminal:

```bash
cd /opt/sktrader
git pull --ff-only origin main
sudo bash deploy/setup-discord-limit-vnc5906.sh inspect
sudo bash deploy/setup-discord-limit-vnc5906.sh prepare-monitor
sudo bash deploy/setup-discord-limit-vnc5906.sh status
```

The monitor uses a dedicated Python virtual environment at
`/var/lib/dkcapital-desktop/.venv-discord-limit`. The installer creates it
as `dkvnc` and installs only `websockets>=14,<17`, matching the project's
existing Python dependency declaration. The shared `/opt/sktrader/.venv`
used by existing scanner services is **not modified**. If the VPS Python
lacks the `venv` module or internet package access, the script exits
without installing the monitor; inspect the error before changing host
packages.

The install uses the pre-existing secure token file at
`/var/lib/dkcapital-mt5-3/.local/share/sktrader/discord-bot-token`
if present and copies it into `dkvnc`'s private file, without printing
token contents. It makes a read-only GET request to verify the bot can
access channel `1565527578163552327`, then starts the isolated
`sktrader-discord-limit-monitor.service` on boot.

Monitor service outputs only to:

```text
/var/lib/dkcapital-desktop/.local/share/sktrader/discord-limit-monitor.csv
```

This is deliberately **not** the EA's actionable file in
`.../MetaQuotes/Terminal/Common/Files/discord_limit_30m_signals.csv`.
No old JIFU, AUROUX or MT5 trading service is restarted. No synthetic
Discord messages are posted, and no orders can be created by this monitor.

## Verify before actual trading cutover

View live logs:

```bash
sudo journalctl -u sktrader-discord-limit-monitor.service -n 40 --no-pager
```

Expected: `Discord Gateway connected; channel ... author ...`.
The default trusted author is the Discord bot itself. If signals are
actually posted by another bot, its exact Discord user ID must be used
via the explicit `--author-id` parameter and the MESSAGE_CONTENT
privileged intent must be enabled where necessary. Do not disable
trusted-author restrictions.

For a genuine future signal, verify a `Limit signal queued:` log and
the corresponding five fields and original 30-minute expiry in the
monitor CSV, without printing credentials or changing the live MT5 feed.
Do not generate synthetic trading signals on any armed account.

Only after verifying signal author, freshness, correct broker instrument
and risk rules should the separate service cutover use the actual
MT5 Common/Files path. Start live routing with an empty new database
so monitor-recorded signals are never backfilled as fresh orders.
The unified EA requires a hedging account to place overlapping orders;
netting accounts are telemetry-only. A live demo/real login is not
hardcoded. The user must deliberately enable `EnableNewEntries`
and MT5 Algo Trading separately.

If the secure token source is not available, the prepare command
fails closed. Do not paste tokens into GitHub, this chat, or the terminal
command line.
