#!/usr/bin/env bash
# Prepare MT5 for the new XFCE VNC desktop only, without touching trading EAs.
set -Eeuo pipefail
umask 077
user=dkvnc
home=/var/lib/dkcapital-desktop
prefix="$home/.wine-mt5"
downloads="$home/Downloads"
bin="$home/bin"
desktop="$home/Desktop"
exe="$downloads/mt5setup.exe"
service=/etc/systemd/system/dkcapital-desktop-vnc.service
compiler=dkcapital-mt5-compiler-vnc
url=https://download.mql5.com/cdn/web/metaquotes.software.corp/mt5/mt5setup.exe
die() { echo "ERROR: $*" >&2; exit 1; }

case "$*" in
  status)
    systemctl status dkcapital-desktop-vnc.service --no-pager -n 8 || true
    for file in "$exe" "$prefix/drive_c/Program Files/MetaTrader 5/terminal64.exe"; do
      if [[ -s "$file" ]]; then ls -lh "$file"; else echo "Not present: $file"; fi
    done
    wine --version 2>/dev/null || true
    exit 0
    ;;
  prepare) ;;
  *) die "Usage: sudo bash deploy/install-mt5-vnc5906.sh {status|prepare}" ;;
esac
[[ $(id -u) == 0 ]] || die "Run with sudo from root VPS SSH, not VNC"
[[ -f "$service" ]] || die "New VNC 5906 service not installed"
grep -Fq 'User=dkvnc' "$service" || die "Wrong VNC service user"
grep -Fq -- '-rfbport 5906 -localhost yes' "$service" || die "Wrong VNC binding"
systemctl is-active --quiet dkcapital-desktop-vnc.service || die "VNC desktop not active"
[[ "$(getent passwd "$user" | cut -d: -f6)" == "$home" ]] || die "Wrong desktop home"
[[ "$(docker inspect -f '{{.State.Running}}' "$compiler" 2>/dev/null || true)" == true ]] ||
  die "Isolated compiler is not running; refusing software changes"
[[ ! -d "$prefix" ]] || die "Dedicated MT5 Wine prefix already exists, refusing overwrite"
[[ ! -e "$bin/mt5" && ! -e "$bin/install-mt5" ]] ||
  die "MT5 launcher exists already, refusing overwrite"
avail=$(df -Pk "$home" | awk 'NR==2 {print $4}')
[[ "$avail" =~ ^[0-9]+$ ]] && (( avail > 2500000 )) ||
  die "Need at least 2.5 GB free disk space"

echo "===== INSTALL HOST WINE PACKAGES ====="
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y --no-upgrade --no-install-recommends \
  wine wine64 winbind fonts-wine libegl1 libgl1 curl ca-certificates
command -v wine >/dev/null || die "wine executable missing"
install -d -m 0700 -o "$user" -g "$user" "$downloads" "$bin" "$desktop"

echo "===== DOWNLOAD OFFICIAL METAQUOTES MT5 ====="
[[ ! -e "$exe" ]] || die "Installer already exists, refusing overwrite"
tmp=$(mktemp "$downloads/.mt5setup.XXXXXXXX")
trap 'rm -f -- "$tmp"' EXIT
curl --fail --location --show-error --silent --retry 2 \
  --connect-timeout 15 --max-time 180 "$url" --output "$tmp"
python3 - "$tmp" <<'PY'
from pathlib import Path
import sys
f=Path(sys.argv[1])
if f.stat().st_size<100000 or f.open("rb").read(2)!=b"MZ":
    raise SystemExit("Downloaded file is not a plausible Windows installer")
print("Verified installer PE header, bytes:",f.stat().st_size)
PY
install -o "$user" -g "$user" -m 0600 "$tmp" "$exe"
sha256sum "$exe"

cat > "$bin/install-mt5" <<'INSTALL'
#!/usr/bin/env bash
set -Eeuo pipefail
[[ "$(id -un)" == dkvnc ]] || { echo "Run as dkvnc in VNC desktop"; exit 1; }
[[ -n "$(printenv DISPLAY || true)" ]] || { echo "Open XFCE Terminal first"; exit 1; }
export HOME=/var/lib/dkcapital-desktop
export WINEPREFIX="$HOME/.wine-mt5"
export WINEARCH=win64
echo "Launching official MetaTrader 5 GUI installer in the separate Wine prefix."
exec wine "$HOME/Downloads/mt5setup.exe"
INSTALL

cat > "$bin/mt5" <<'RUN'
#!/usr/bin/env bash
set -Eeuo pipefail
[[ "$(id -un)" == dkvnc ]] || { echo "Run as dkvnc in VNC desktop"; exit 1; }
[[ -n "$(printenv DISPLAY || true)" ]] || { echo "Open XFCE Terminal first"; exit 1; }
export HOME=/var/lib/dkcapital-desktop
export WINEPREFIX="$HOME/.wine-mt5"
export WINEARCH=win64
base="$WINEPREFIX/drive_c/Program Files/MetaTrader 5"
if [[ -s "$base/terminal64.exe" ]]; then
  target="$base/terminal64.exe"
elif [[ -s "$base/terminal.exe" ]]; then
  target="$base/terminal.exe"
else
  echo "MT5 is not installed. Run ~/bin/install-mt5 first." >&2
  exit 1
fi
exec wine "$target"
RUN

cat > "$desktop/Install MetaTrader 5.desktop" <<'ICON'
[Desktop Entry]
Version=1.0
Type=Application
Name=Install MetaTrader 5
Exec=/var/lib/dkcapital-desktop/bin/install-mt5
Icon=applications-system
Terminal=false
Categories=Office;
ICON
cat > "$desktop/MetaTrader 5.desktop" <<'ICON'
[Desktop Entry]
Version=1.0
Type=Application
Name=MetaTrader 5
Exec=/var/lib/dkcapital-desktop/bin/mt5
Icon=applications-office
Terminal=false
Categories=Office;
ICON
chown "$user:$user" "$bin/install-mt5" "$bin/mt5" "$desktop/"*.desktop
chmod 0700 "$bin/install-mt5" "$bin/mt5" "$desktop/"*.desktop

echo "READY. On the new VNC XFCE terminal run: ~/bin/install-mt5"
echo "Complete the MT5 GUI installation; then run: ~/bin/mt5"
echo "Dedicated prefix: $prefix"
echo "No broker login, EA activation or existing MT5 service change occurred."
