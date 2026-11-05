# JIFU watcher: repeated frozen relative-age labels on 5 November 2026

## Observed symptoms

`sktrader.service` and its Playwright Chromium browser were active, and
`sktrader-display.service` was also active. Nevertheless, the watcher
repeatedly reported:

- `JIFU feed age stopped updating: newest alert still says 'just now'`
- `JIFU feed age stopped updating: newest alert still says '6 minutes ago'`
- `Newest visible JIFU alert cannot be extracted; preserving the last snapshot`

On 5 November it continued delivering SNIPER PRO alerts at 16:11 and
16:18 UTC. The continuous restart/reload cycle was driven primarily by
the feed-age watchdog: it rejected *all* otherwise-readable alert cards
because an on-screen relative age label did not advance within 120/240
seconds, causing reloads every few minutes. Reloading the JIFU React
dashboard also temporarily removes the alert DOM and can trigger
additional extraction errors.

## Change

A stale relative-age label alone is now **advisory**, not a reason to
reject the available latest alert cards. Every poll continues through
`ScannerStore.observe` and the existing Discord outbox when current
visible cards are extractable and pass the independent fingerprint,
overlap and XAUUSD/scanner checks. Warnings are limited to one per 5
minutes. If the age remains unchanged for 10 minutes, one controlled
JIFU dashboard refresh occurs **after** pending cards are processed,
then the age timer is reset. The refresh shares the existing hard-error
reload cooldown, avoiding a second immediate reload while React loads.

This is **not** permission to infer new alerts when the screen is
unreadable. Missing newest POI, malformed cards, wrong scanner selector,
session logout, lost ordered overlap and browser transport exceptions
still fail closed. The persisted SQLite snapshot, deduplication, source
channels, any pending Discord outbox messages and all JIFU login
credentials remain untouched. No GWS, AUROUX, MT5 or Discord trading
listener change is part of this patch.

Relative-age text is not a reliable source heartbeat, so this patch
cannot prove whether the upstream JIFU feed is actually updating.
The warnings remain visible for investigation. During a true source
outage, some alerts may still be missed if they disappear from JIFU's
short visible feed before reconnecting.

## Deploy

The existing watcher runs under `sktrader.service`. Once this PR is
merged, on the VPS as root:

```bash
cd /opt/sktrader
git pull --ff-only origin main
sudo systemctl restart sktrader.service
sudo systemctl status sktrader.service --no-pager -l -n 12
sudo journalctl -u sktrader.service --since '5 minutes ago' --no-pager | tail -75
```

This restarts the **JIFU watcher only**. It must leave
`sktrader-discord-limit-live.service`, the live MT5 EA,
`dkcapital-desktop-vnc.service`, broker orders and legacy systems
unchanged. The watcher will reuse its existing browser profile and
SQLite signal cursor, without deliberate backfilling.

Expected log: `Starting XAUUSD-only scanner monitor...` and
`JIFU Alerts sidebar unfiltered...`. If a static relative-age label
reappears, look for `JIFU relative age label stalled; checking valid
scanner cards anyway` instead of repeated `Monitor paused` and
`Reloading stalled...` every 2 to 4 minutes.

If `Newest visible JIFU alert cannot be extracted` continues despite
restart, inspect the browser diagnostic and JIFU website directly:
this patch does not fabricate missing alerts or bypass site
authentication.
