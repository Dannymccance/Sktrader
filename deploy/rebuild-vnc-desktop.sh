#!/usr/bin/env bash
# One new clipboard-enabled desktop. Retire only known OLD VNC viewers.
# Never stop MT5 terminals, delete broker Wine data, or touch compiler VNC 5903.
set -Eeuo pipefail
umask 077

NEW_UNIT=/etc/systemd/system/dkcapital-desktop-vnc.service
NEW_USER=dkvnc
NEW_HOME=/var/lib/dkcapital-desktop
COMPILER=dkcapital-mt5-compiler-vnc
die() { echo "ERROR: $*" >&2; exit 1; }

status() {
  echo "===== KNOWN VNC SERVICES ====="
  for name in dkcapital-vnc1 dkcapital-vnc2 dkcapital-vnc3 dkcapital-desktop-vnc; do
    printf '%s: ' "$name"
    systemctl is-active "$name.service" 2>/dev/null || true
  done
  echo "===== COMPILER VNC (PRESERVED) ====="
  docker ps -a --filter "name=^/$COMPILER$" \
    --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}' || true
  echo "===== VNC PORTS ====="
  ss -lntp | grep -E ':59[0-9][0-9]([[:space:]]|$)' || true
  echo "===== MT5 (NOT MANAGED BY THIS SCRIPT) ====="
  for name in dkcapital-mt5-1 dkcapital-mt5-2 dkcapital-mt5-3 sktrader-display; do
    printf '%s: ' "$name"
    systemctl is-active "$name.service" 2>/dev/null || true
  done
}
case "$*" in
  status) status; exit 0;;
  apply) ;;
  *) die "Usage: sudo bash deploy/rebuild-vnc-desktop.sh {status|apply}";;
esac

[[ $(id -u) == 0 ]] || die "Run using sudo"
command -v docker >/dev/null || die "Docker missing; cannot verify compiler"
[[ "$(docker inspect -f '{{.State.Running}}' "$COMPILER" 2>/dev/null || true)" == true ]] ||
  die "Compiler VNC is not running; refusing cleanup"
[[ ! -e "$NEW_UNIT" ]] || die "New VNC service already installed; refusing overwrite"
[[ ! -S /tmp/.X11-unix/X5 ]] || die "X display :5 already occupied"
ss -lnt | grep -Eq ':5905[[:space:]]' && die "Port 5905 occupied"

# Do not disable unfamiliar services that happen to have similar names.
for line in "dkcapital-vnc1 5901 :150" "dkcapital-vnc2 5902 :151" "dkcapital-vnc3 5904 :152"; do
  read -r unit port display <<< "$line"
  path=$(systemctl show -p FragmentPath --value "$unit.service" 2>/dev/null || true)
  if [[ -n "$path" && "$path" != /dev/null && -f "$path" ]]; then
    grep -Fq -- "-rfbport $port -localhost" "$path" ||
      die "Unrecognized $unit port configuration: $path"
    grep -Fq -- "-display $display" "$path" ||
      die "Unrecognized $unit display configuration: $path"
  fi
done

echo "===== INSTALLING ONE NEW DESKTOP AND CLIPBOARD TOOLS ====="
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y --no-install-recommends \
  tigervnc-standalone-server tigervnc-common tigervnc-tools \
  xfce4-session xfwm4 xfce4-panel xfdesktop4 xfce4-settings \
  xfce4-terminal thunar dbus-x11 autocutsel xclip xauth
for cmd in tigervncserver tigervncpasswd tigervncconfig autocutsel startxfce4 xclip; do
  command -v "$cmd" >/dev/null || die "Missing required program $cmd"
done

if ! id "$NEW_USER" >/dev/null 2>&1; then
  useradd --system --create-home --home-dir "$NEW_HOME" --shell /bin/bash "$NEW_USER"
fi
[[ "$(getent passwd "$NEW_USER" | cut -d: -f6)" == "$NEW_HOME" ]] ||
  die "Unexpected new-desktop user home"
install -d -o "$NEW_USER" -g "$NEW_USER" -m 0700 "$NEW_HOME/.vnc"

echo
echo "Create your NEW private VNC password, directly in this SSH terminal."
echo "VNCAuth uses the first eight characters. SSH tunneling provides encryption."
read -r -s -p "VNC password: " vnc_pass < /dev/tty
echo
[[ $(printf %s "$vnc_pass" | wc -c) -ge 8 ]] ||
  die "Choose at least eight password characters"
printf '%s\n' "$vnc_pass" | tigervncpasswd -f > "$NEW_HOME/.vnc/passwd"
unset vnc_pass
chown "$NEW_USER:$NEW_USER" "$NEW_HOME/.vnc/passwd"
chmod 0600 "$NEW_HOME/.vnc/passwd"

cat > "$NEW_HOME/.vnc/xstartup" <<'STARTUP'
#!/usr/bin/env bash
unset SESSION_MANAGER
unset DBUS_SESSION_BUS_ADDRESS
export XDG_CURRENT_DESKTOP=XFCE
/usr/bin/tigervncconfig -nowin &
/usr/bin/autocutsel -fork -selection CLIPBOARD
/usr/bin/autocutsel -fork -selection PRIMARY
exec /usr/bin/dbus-run-session -- /usr/bin/startxfce4
STARTUP
chown "$NEW_USER:$NEW_USER" "$NEW_HOME/.vnc/xstartup"
chmod 0700 "$NEW_HOME/.vnc/xstartup"

cat > "$NEW_UNIT" <<'UNIT'
[Unit]
Description=DK Capital fresh clipboard-enabled VNC desktop (SSH localhost only)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=dkvnc
Group=dkvnc
WorkingDirectory=/var/lib/dkcapital-desktop
Environment=HOME=/var/lib/dkcapital-desktop
ExecStart=/usr/bin/tigervncserver -fg :5 -rfbport 5905 -localhost yes -SecurityTypes VncAuth -PasswordFile /var/lib/dkcapital-desktop/.vnc/passwd -geometry 1920x1080 -depth 24 -xstartup /var/lib/dkcapital-desktop/.vnc/xstartup
Restart=on-failure
RestartSec=5
NoNewPrivileges=true
UMask=0077

[Install]
WantedBy=multi-user.target
UNIT
chmod 0644 "$NEW_UNIT"
systemctl daemon-reload
systemctl enable --now dkcapital-desktop-vnc.service
sleep 4
systemctl is-active --quiet dkcapital-desktop-vnc.service || {
  journalctl -u dkcapital-desktop-vnc -n 40 --no-pager
  die "New desktop failed; legacy VNC services remain unchanged"
}
ss -lnt | grep -Eq '127\.0\.0\.1:5905[[:space:]]' ||
  die "New desktop is not on localhost:5905; old services remain unchanged"
if ! timeout 10 runuser -u "$NEW_USER" -- env HOME="$NEW_HOME" DISPLAY=:5 \
  sh -c 'printf DK_VNC_CLIPBOARD_OK | xclip -selection clipboard &&
         test "$(xclip -o -selection clipboard)" = DK_VNC_CLIPBOARD_OK'; then
  die "New clipboard self-test failed; legacy services remain unchanged"
fi
echo "NEW desktop :5, port 5905 and X11 clipboard self-test PASSED."

# Only after the new desktop has started and passed its clipboard self-test.
backup="/root/old-vnc-services-$(date -u +%Y%m%dT%H%M%SZ)"
install -d -m 0700 "$backup"
for unit in dkcapital-vnc1 dkcapital-vnc2 dkcapital-vnc3; do
  path=$(systemctl show -p FragmentPath --value "$unit.service" 2>/dev/null || true)
  systemctl disable --now "$unit.service" 2>/dev/null || true
  if [[ "$path" == "/etc/systemd/system/$unit.service" && -f "$path" ]]; then
    mv "$path" "$backup/$unit.service"
  fi
done
systemctl daemon-reload
[[ "$(docker inspect -f '{{.State.Running}}' "$COMPILER" 2>/dev/null || true)" == true ]] ||
  die "Protected compiler changed state unexpectedly"
echo "Retired known old viewer services 5901, 5902, 5904."
echo "Archived their service files: $backup"
echo "Kept compiler 5903, MT5 data/services and JIFU display unchanged."
echo "Other unrecognized VNC processes, if any, were not killed."
status
