# Automatic recovery for DK Capital unified MT5 VNC 5906

The complete Discord -> MT5 -> MetaQuotes-Demo pipeline has placed a broker-
accepted XAUUSD SELL LIMIT (0.71 lots) at 4167.53 with SL 4175.03 and TP
4152.53. This watchdog is scoped to the **isolated** `dkvnc` MT5 terminal,
its production Discord listener, and its VNC 5906 desktop. It does not modify
the separate GWS, AUROUX, compiler or JIFU scanner installations.

## Recovery behavior

| Failure | Automatic action |
| --- | --- |
| Discord listener exits | Existing `Restart=on-failure` restarts it |
| Discord listener active, but Gateway READY/heartbeat ACK stale | Recovery timer restarts only `sktrader-discord-limit-live.service` |
| VPS reboot | Production Discord + VNC enabled; timer checks for missing MT5 and launches it as `dkvnc` |
| MT5 process absent | After two checks, relaunch only if VNC and Gateway healthy and MT5 signal CSV refreshed recently |
| MT5 process exists, but account telemetry stops | Alert only. Do NOT kill/restart Wine: pending orders and trades may still be active |
| VNC 5906 stopped | Restart only after two checks and only if MT5 process is absent |
| JIFU `sktrader.service` stops | Alert only. Its existing service has independent `Restart=always` and credential/session handling |
| Repeated failures | No more than 3 recovery attempts per component per rolling hour, minimum 4 to 5 minutes between attempts |

The watchdog is a root-owned oneshot systemd service driven by a 30-second
timer. It keeps cooldown state in `/var/lib/dkcapital-recovery/watchdog.json`
and uses a kernel file lock to prevent overlapping checks. It never submits,
modifies or cancels broker orders. Recovery alerts always appear in the journal.

**Signal freshness:** The production Discord bridge has a new, optional
`--max-input-age-sec 60` flag, installed here. Only signals **no more than
60 seconds old** are eligible to appear in the EA's input file. This prevents
a restarted MT5 from executing a backlogged 10- or 20-minute-old signal.
Valid pending orders **still expire 30 minutes after the original Discord
message**. This is an intentional tightening of the order *submission*
window, not a change to pending order expiry. It may skip messages during
an outage, prioritizing no stale entries over attempting to catch up.

Gateway health comes from a private `--health-file` that records READY,
RESUMED and Discord HEARTBEAT ACK events without depending on new signal
messages. The watcher compares it with systemd's actual listener PID to
avoid considering an old connection's health file valid after a crash.
Health JSON is separate from the actionable MT5 signal CSV.

## Installation on the VPS

Run once as root after pulling merged GitHub changes:

```bash
cd /opt/sktrader
git pull --ff-only origin main
sudo bash deploy/install-unified-recovery-vnc5906.sh inspect
sudo bash deploy/install-unified-recovery-vnc5906.sh install
sudo bash deploy/install-unified-recovery-vnc5906.sh status
```

The installer verifies the existing production listener's exact channel
`1565527578163552327`, author IDs `1554225452091048017` and
`508740193369587722`, unique live DB and MT5 output path.
It preserves the existing original cutover epoch and SQLite history.
It installs an **ExecStart drop-in for only that one listener**, adding
`--health-file` and `--max-input-age-sec 60`. This requires **one
production Discord listener restart**, which might miss signals delivered
during its brief reconnect. The original 30-minute pending broker orders
are unaffected. It checks a fresh READY health file before enabling the
timer. It neither restarts MT5, VNC, JIFU nor the compiler.

Do not rerun the one-time original
`deploy/live-discord-limit-vnc5906.sh stage` or `start` actions.
Do not rerun the recovery installer after it succeeds, because it refuses
to overwrite an existing drop-in.

## View status

```bash
sudo python3 /opt/sktrader/deploy/unified_recovery_vnc5906.py status
sudo systemctl status dkcapital-unified-recovery.timer --no-pager
sudo journalctl -u dkcapital-unified-recovery.service -n 60 --no-pager
sudo journalctl -u sktrader-discord-limit-live.service -n 30 --no-pager
```

A successful systemd timer means monitoring is *installed*, not that every
subsystem is healthy. Watch for `gateway_healthy: true`, fresh MT5
telemetry, a running MT5 process, and active VNC.

## Optional Discord outage alerts

Recovery always records DOWN / RECOVERED messages locally. To send them
via the existing DK Capital bot, create a **separate admin-only Discord
channel** which does NOT receive trading instructions. Never reuse the
trading signal channel. Copy its numeric channel ID and privately create:

```bash
sudo install -o root -g root -m 0600 /dev/null /etc/dkcapital-recovery-admin.conf
sudo nano /etc/dkcapital-recovery-admin.conf
```

Enter exactly one line, with your *actual* admin channel ID:

```text
RECOVERY_ADMIN_CHANNEL_ID=123456789012345678
```

The sample numeric value is illustrative and must be replaced. The bot
needs View Channel and Send Messages in that channel. No token is placed
in this configuration: alerts reuse the existing private dkvnc bot token.
The watchdog refuses to post alert messages to source channel
`1565527578163552327`. Alert messages are plain non-trading text;
messages contain no signals, passwords or token values.

## Emergency stop and recovery limits

To **intentionally keep trading offline**, stop the timer FIRST:

```bash
sudo systemctl disable --now dkcapital-unified-recovery.timer
sudo systemctl stop sktrader-discord-limit-live.service
```

Stopping the live feed prevents *future* ingestion only. It **does not
cancel pending orders or close positions**. Separately disable MT5 EA
entries in the GUI if needed and inspect existing broker orders.

To reactivate:

```bash
sudo systemctl start sktrader-discord-limit-live.service
sudo systemctl enable --now dkcapital-unified-recovery.timer
```

The watchdog never force-kills MT5, modifies the broker account, overrides
Algo Trading permissions, guesses reauthentication credentials or bypasses
2FA. The terminal relaunch can succeed at the OS level without successfully
restoring broker login, chart/EAs or automated trading. The next telemetry
check then reports degraded health and requires manual review. An MT5
terminal that is still running but frozen cannot be safely distinguished
from one with active broker exposure without additional trusted broker
order-state checks, so the watchdog alerts rather than forcing a restart.
