# Persistent VPS scanner (watch only)

This installation is limited to /opt/sktrader and its dedicated X11 display
:155. It does not modify DK Capital Docker, MT5, the EA or trading rules.

After GitHub CI passes and the PR is merged:

1. SSH into the VPS and run:

       cd /opt/sktrader
       git pull --ff-only origin main
       install -m 0644 deploy/sktrader-display.service /etc/systemd/system/sktrader-display.service
       install -m 0644 deploy/sktrader.service /etc/systemd/system/sktrader.service
       systemctl daemon-reload
       systemctl enable --now sktrader-display.service
       systemctl enable --now sktrader.service

2. Check:

       systemctl status --no-pager sktrader-display sktrader
       journalctl -u sktrader -n 60 --no-pager

3. Verify new XAUUSD signals are delivered to the original eight
   *-signals channels. The first mixed feed read is a baseline; it does
   not replay historical alerts. If the JIFU sidebar is set to a
   specific scanner, set it back to Select Scanner and All.

4. To inspect after disconnecting SSH:

       systemctl is-active sktrader
       journalctl -u sktrader --since "10 minutes ago" --no-pager

5. To stop/restart deliberately:

       systemctl stop sktrader
       systemctl restart sktrader

The watcher is started by systemd and no longer depends on a terminal
session or user's PC. It will automatically restart if Chromium or
Python exits and will start on VPS reboot. The dedicated display service
reuses existing Xvfb :155 without killing it. If :155 is not present
after reboot, the service recreates only this isolated display.

The Xvfb service does not start a VNC server. The already-running VNC
daemon can be used while present; after a full reboot it may need its
own systemd management for remote graphical viewing, but the watcher
does not require VNC access.

Potential missed alerts are NOT automatically traded or replayed.
A separate one-time manual replay command can retrieve only historical
cards still visible to JIFU; ensure watcher is stopped during replay,
and restart it afterwards. A screenshot alone does not prove all
alerts during the outage are recoverable.
