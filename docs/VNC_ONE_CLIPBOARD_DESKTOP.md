# Reset DK Capital VNC access to one clean clipboard desktop

Purpose: keep the isolated MetaEditor compiler on **5903**, retire the
three known historical x11vnc observation viewers on 5901, 5902 and 5904,
and create ONE new TigerVNC/XFCE desktop on **5906**. Port **5905** was\noccupied on the VPS, so we preserve that unidentified listener until inspected.\nA viewer labelled `jurspan-ai:155` is displaying the existing JIFU Xvfb\nscreen, NOT this new desktop.

The new desktop is a separate unprivileged Linux account named `dkvnc`.
It does not import broker credentials, MT5 account data, or a Wine prefix.
The previously failed and inactive MT5 service states are NOT fixed by
retiring VNC viewer services. JIFU's Xvfb display :155 is not a VNC viewer
and is left alone because the scanner collector uses it.

## Setup from VPS SSH

First inspect current services (read-only). The new desktop uses port 5906\nbecause the VPS already had something bound on 5905:

```bash
cd /opt/sktrader
git pull --ff-only origin main
sudo bash deploy/rebuild-vnc-desktop.sh status
```

Then run:

```bash
sudo bash deploy/rebuild-vnc-desktop.sh apply
```

The installer asks for a **NEW VNC password directly on your SSH terminal**.
Do not send it through Discord or ChatGPT. For VncAuth only the first eight
characters are significant, so access remains SSH-tunnel-only.

What the command does:

1. Verify compiler container dkcapital-mt5-compiler-vnc is running.
2. Check VNC 5906 and display :5 are free, inspect known old unit signatures.
3. Install Ubuntu TigerVNC, XFCE, tigervncconfig, autocutsel and xclip.
4. Create new private user `dkvnc` and its clipboard-capable desktop
   at DISPLAY=:5, listening only on 127.0.0.1:5906.
5. Check the desktop service and local X11 clipboard.
6. ONLY THEN stop/disable verified legacy **viewer services**
   dkcapital-vnc1, dkcapital-vnc2 and dkcapital-vnc3.
   Archive known units into a timestamped directory under /root.
7. Verify compiler still running and display current service status.

The script does not stop/restart account MT5 services, remove their Wine
files, kill unknown VNC processes, or stop sktrader-display. It does not
touch the compiler 5903 port/container. Any unknown VNC listener still
visible in the final inventory must be reviewed individually, not
blindly terminated.

## Connect from Windows

In a **separate PowerShell window**, run:

```powershell
ssh -N -L 5906:127.0.0.1:5906 root@31.187.72.71
```

Leave that tunnel open. Open TigerVNC Viewer (or a clipboard-compatible
viewer) and connect to `127.0.0.1::5906`. Enter the new VNC password.

The VNC server is not exposed on the public network and should not
require a VPS firewall rule.

## Verify clipboard copy/paste both ways

1. Copy `WINDOWS_TO_VNC_123` from Windows.
2. Focus XFCE Terminal inside VNC and press **Ctrl+Shift+V**.
   It should paste the exact token. Inside the VNC terminal, run
   `xclip -o -selection clipboard` to inspect current clipboard.
3. Copy `VNC_TO_WINDOWS_456` from VNC terminal with **Ctrl+Shift+C**.
4. Paste into Windows Notepad using **Ctrl+V**.

If a direction does not work, check the VNC viewer's
**Send clipboard / Accept clipboard** settings. The installer's
self-test verifies only the Linux X11 clipboard; it cannot prove
end-to-end Windows sync without an actual viewer connection.

## After the fresh desktop

A functioning VNC desktop is not an MT5 trading installation. Install
a fresh MT5/Wine instance under that new user only after verifying the
correct broker account, then compile/deploy the unified EA on its proper
data path. Do not reuse the stale MetaQuotes demo CSV as proof of REAL
account identity. Do not switch the Discord bridge until that terminal
and its Common Files destination are confirmed.

Keep the GWS and AUROUX EAs/broker services separate from this UI reset.
