#!/usr/bin/env bash
# Repair ONLY the new :5/5905 VNC desktop when RFB connects but shows black.
# No trading services, MT5 Wine prefixes, compiler VNC or JIFU touched.
set -Eeuo pipefail
umask 077

unit=dkcapital-desktop-vnc.service
config=/etc/systemd/system/dkcapital-desktop-vnc.service
user=dkvnc
home=/var/lib/dkcapital-desktop
startup="$home/.vnc/xstartup"

fail() { echo "ERROR: $*" >&2; exit 1; }
[[ "$(id -u)" == 0 ]] || fail "Run with sudo"

echo "===== CURRENT NEW VNC STATE ====="
systemctl status "$unit" --no-pager --lines=16 || true
echo "===== RECENT DESKTOP ERRORS ====="
journalctl -u "$unit" --no-pager -n 40 || true
find "$home/.vnc" -maxdepth 1 -type f \( -name '*.log' -o -name '*.out' \) \
   -print -exec tail -20 {} \; 2>/dev/null || true

# This is a narrowly scoped repair: never repair a different VNC service.
[[ -f "$config" && -f "$startup" ]] || fail "Expected new VNC unit or desktop startup missing"
[[ "$(systemctl show -p FragmentPath --value "$unit")" == "$config" ]] ||
    fail "Unexpected VNC service location"
grep -Fq 'User=dkvnc' "$config" || fail "Unexpected VNC service user"
grep -Fq -- '-rfbport 5905 -localhost yes' "$config" ||
    fail "Unexpected VNC port or network binding"
grep -Fq -- '-xstartup /var/lib/dkcapital-desktop/.vnc/xstartup' "$config" ||
    fail "Unexpected VNC desktop startup script"
[[ "$(getent passwd "$user" | cut -d: -f6)" == "$home" ]] ||
    fail "Unexpected desktop home path"
[[ -d "$home/.vnc" ]] || fail "Private desktop VNC folder missing"
[[ -s "$home/.vnc/passwd" ]] || fail "VNC password file missing"

echo "===== REPAIR XFCE SESSION, PRESERVING VNC PASSWORD ====="
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y --no-install-recommends \
    xfce4-session xfwm4 xfce4-panel xfdesktop4 xfce4-settings \
    xfce4-terminal dbus-x11 xterm tigervnc-common tigervnc-standalone-server \
    autocutsel xclip x11-utils

for cmd in /usr/bin/dbus-run-session /usr/bin/xfce4-session /usr/bin/xterm \
           /usr/bin/tigervncconfig /usr/bin/autocutsel; do
    [[ -x "$cmd" ]] || fail "Missing desktop component: $cmd"
done

stamp=$(date -u +%Y%m%dT%H%M%SZ)
backup="$home/.vnc/xstartup.backup.$stamp"
cp -p "$startup" "$backup"
cat > "$startup" <<'STARTUP'
#!/usr/bin/env bash
# TigerVNC provides DISPLAY and XAUTHORITY itself.
unset SESSION_MANAGER
unset DBUS_SESSION_BUS_ADDRESS
export XDG_SESSION_TYPE=x11
export XDG_CURRENT_DESKTOP=XFCE
export DESKTOP_SESSION=xfce

# Preserve bidirectional VNC and X11 PRIMARY/CLIPBOARD synchronization.
/usr/bin/tigervncconfig -nowin &
/usr/bin/autocutsel -fork -selection CLIPBOARD || true
/usr/bin/autocutsel -fork -selection PRIMARY || true

# Always provide a visible window, even if XFCE panel fails to load.
/usr/bin/xterm -title "DK Capital desktop - copy and paste test" \
    -geometry 110x35+55+55 &

# Start the desktop directly. startxfce4 can invoke a second X11 startup
# layer under an existing TigerVNC X server and leave an empty desktop.
/usr/bin/dbus-run-session -- /usr/bin/xfce4-session \
    >> "$HOME/.vnc/xfce-session.log" 2>&1
echo "XFCE session exited; leaving diagnostic terminal running" \
    >> "$HOME/.vnc/xfce-session.log"
exec /usr/bin/xterm -title "XFCE failed - check ~/.vnc/xfce-session.log"
STARTUP
chown "$user:$user" "$startup"
chmod 0700 "$startup"
bash -n "$startup" || fail "New VNC xstartup has syntax errors"

echo "===== RESTARTING NEW DESKTOP SERVICE ONLY ====="
systemctl restart "$unit"
sleep 5
systemctl is-active --quiet "$unit" || {
    journalctl -u "$unit" --no-pager -n 55 || true
    echo "Original xstartup backed up to $backup" >&2
    fail "New VNC service did not remain active"
}
ss -lnt | grep -Eq '127\.0\.0\.1:5905[[:space:]]' ||
    fail "New VNC server is not listening on localhost port 5905"

echo "===== XFCE PROCESS CHECK ====="
pgrep -a -u "$user" -f 'xfce4-session|xfwm4|xfdesktop|xfce4-panel|xterm' || true
echo "===== X11 WINDOW CHECK ====="
runuser -u "$user" -- env HOME="$home" DISPLAY=:5 \
    xwininfo -root -tree 2>&1 | tail -35 || true
echo "===== XFCE SESSION LOG ====="
if [[ -f "$home/.vnc/xfce-session.log" ]]; then
    tail -40 "$home/.vnc/xfce-session.log"
fi

echo "===== PROTECTED SYSTEMS UNCHANGED ====="
docker ps -a --filter 'name=^/dkcapital-mt5-compiler-vnc$' \
    --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}' || true
echo "Only the new :5 / 5905 desktop service was restarted."
echo "VNC password was not changed. Reconnect to 127.0.0.1::5905 through SSH."
echo "Previous xstartup backup: $backup"
