# JIFU recovery, login and alert health

This is a watch-only XAUUSD update. It does not change DK Capital's MT5, broker, EA or signal routing.

## Recovery

- After 12 consecutive failed feed reads, the watcher reloads the dashboard at most once per 120 seconds.
- It retains the previous durable SQLite snapshot. It will not silently baseline a blank page or invent overlap across disconnected historical feeds.
- On a confirmed login form, it tries the existing browser session and optionally a dedicated local JIFU credentials file. If login requires CAPTCHA, 2FA or unfamiliar controls, it pauses and needs the user to authenticate interactively.
- Session-login retries are bounded at 120 seconds.
- Existing signals queued in SQLite remain pending for Discord if delivery fails.
- Hourly local coverage logs show the last time each source was visible and the last time a new XAUUSD signal was detected. This is not proof a scanner has not issued a signal, because the mixed feed is still JIFU's visible subset.

## Private credentials (optional for automatic sign-in)

Do NOT send your password to ChatGPT, GitHub or Discord.

While logged into the VPS, run the following script. It prompts locally without echoing the password. It writes a private JSON file as the collector user.

    runuser -u dkmt5c -- env HOME=/var/lib/dkcapital-mt5-3 /opt/sktrader/.venv/bin/python -c 'import getpass,json,os,pathlib; p=pathlib.Path("/var/lib/dkcapital-mt5-3/.local/share/sktrader/jifu-credentials.json"); p.parent.mkdir(parents=True,exist_ok=True); u=input("JIFU email: "); w=getpass.getpass("JIFU password (hidden): "); fd=os.open(p,os.O_CREAT|os.O_TRUNC|os.O_WRONLY,0o600); os.fchmod(fd,0o600); os.write(fd,json.dumps({"email":u,"password":w}).encode()); os.close(fd); print("Private JIFU credentials saved")'

Add this line to **/opt/sktrader/.env** (not GitHub):

    SKTRADER_JIFU_CREDENTIALS_FILE=/var/lib/dkcapital-mt5-3/.local/share/sktrader/jifu-credentials.json

If your sign-in screen has nonstandard controls, is SSO-only or requests 2FA, the watcher will fail closed rather than guessing or bypassing protections. Reauthenticate manually with the existing login command and VNC to refresh the browser session.

## Separate administrative health channel (optional)

Create or reuse an existing admin-only Discord channel that is **not** one of the eight scanner signal channels, then set the exact verified ID in /opt/sktrader/.env:

    SKTRADER_HEALTH_CHANNEL_ID=<actual_admin_channel_id>

Only the health channel receives an interruption notice after two minutes of consecutive failures and a recovery notice afterwards. Outages are logged locally even when the health channel is unset. Repeated failure messages are limited to once per hour.

## Deploy

After PR CI passes and the code is merged:

    cd /opt/sktrader
    git pull --ff-only origin main
    systemctl restart sktrader
    systemctl status --no-pager sktrader
    journalctl -u sktrader -n 80 --no-pager

Do not restart Docker, VPS, VNC or DK Capital MT5.

## Limitations

The mixed JIFU website feed is not an authoritative eight-source historical event API. A transient outage can still hide alerts older than the ten cards JIFU exposes. The watcher conservatively refuses unsafe overlap, rather than fabricate or duplicate missed trade alerts. Automatic reload and reauthentication do not guarantee complete historical backfill. All recovered alerts remain watch-only and are never placed as broker orders.
