#!/bin/bash
# ตั้งเครื่อง Ubuntu 24.04 ใหม่ (NUC / OptiPlex) ให้รีโมตได้แบบไม่มีจอ — รันครั้งเดียวในชื่อผู้ใช้ปกติ (ไม่ต้อง sudo):
#   bash setup_nuc_base.sh                 # เข้า ZeroTier วง B (ค่าเริ่มต้น)
#   bash setup_nuc_base.sh <NETWORK_ID>    # เข้าวงอื่น
#
# สิ่งที่ตั้ง (รวมทุกอย่างที่ทำกับ NUC-10 ไว้ในไฟล์เดียว)
#   1) โปรแกรม: curl git openssh-server python3-venv ffmpeg x11vnc nginx xserver-xorg-video-dummy
#   2) ZeroTier + join วง
#   3) GDM: ปิด Wayland + ล็อกอินอัตโนมัติ
#   4) จออัตโนมัติ: บูตแล้วไม่มีจอ -> จอเสมือน 1280x720 / มีจอ HDMI หรือ DP -> ใช้จอจริงตามปกติ
#   5) x11vnc (VNC พอร์ต 5900) เปิดเองตอนบูต
#   6) เน็ตต่อใหม่เองเสมอ + net-watchdog
#   7) ปิด sleep / ล็อกจอ (เครื่อง server)
set -e
NETWORK_ID="${1:-b6079f73c6de1256}"
USER_NAME="$(whoami)"
[ "$USER_NAME" = root ] && { echo "อย่ารันด้วย sudo — รัน: bash $0"; exit 1; }
echo "== ตั้งเครื่องสำหรับผู้ใช้ $USER_NAME  (ZeroTier $NETWORK_ID) =="

# ---- 1) โปรแกรม ----
sudo apt update
sudo apt install -y curl git openssh-server python3-venv python3-pip ffmpeg x11vnc nginx xserver-xorg-video-dummy
sudo systemctl enable --now ssh

# ---- 2) ZeroTier ----
command -v zerotier-cli >/dev/null || curl -s https://install.zerotier.com | sudo bash
sudo systemctl enable --now zerotier-one
sudo zerotier-cli join "$NETWORK_ID" || true

# ---- 3) GDM: ปิด Wayland + ล็อกอินอัตโนมัติ ----
GDM=/etc/gdm3/custom.conf
sudo sed -i '/^#\?\s*AutomaticLogin/d; /^#\?\s*WaylandEnable/d' "$GDM"
sudo sed -i "/^\[daemon\]/a WaylandEnable=false\nAutomaticLoginEnable=True\nAutomaticLogin=$USER_NAME" "$GDM"

# ---- 4) จออัตโนมัติ (ไม่ต้องใช้ตัวหลอกจอ HDMI) ----
# ก่อน GDM เปิด: ถ้าไม่มีพอร์ตไหน connected -> ใส่ไฟล์จอเสมือน 1280x720 / ถ้ามีจอ -> ลบไฟล์ทิ้ง ใช้จอจริง
sudo tee /usr/local/sbin/display-autoselect.sh > /dev/null <<'EOF'
#!/bin/sh
CONF=/etc/X11/xorg.conf.d/10-headless.conf
# รอไดรเวอร์การ์ดจอ (i915) สร้างพอร์ตก่อน สูงสุด 15 วิ
i=0; while [ $i -lt 15 ] && ! ls /sys/class/drm/card*-*/status >/dev/null 2>&1; do sleep 1; i=$((i+1)); done
sleep 2
if grep -qx connected /sys/class/drm/card*-*/status 2>/dev/null; then
  rm -f "$CONF"
  echo "พบจอจริง -> ใช้จอจริง"
else
  mkdir -p /etc/X11/xorg.conf.d
  cat > "$CONF" <<'XORG'
Section "Device"
    Identifier "HeadlessDevice"
    Driver "dummy"
    VideoRam 256000
EndSection
Section "Monitor"
    Identifier "HeadlessMonitor"
    HorizSync 28.0-80.0
    VertRefresh 48.0-75.0
    Modeline "1280x720" 74.25 1280 1390 1430 1650 720 725 730 750 +hsync +vsync
EndSection
Section "Screen"
    Identifier "HeadlessScreen"
    Device "HeadlessDevice"
    Monitor "HeadlessMonitor"
    DefaultDepth 24
    SubSection "Display"
        Depth 24
        Modes "1280x720"
    EndSubSection
EndSection
XORG
  echo "ไม่พบจอ -> จอเสมือน 1280x720"
fi
EOF
sudo chmod +x /usr/local/sbin/display-autoselect.sh
sudo tee /etc/systemd/system/display-autoselect.service > /dev/null <<'EOF'
[Unit]
Description=Pick real monitor or 1280x720 virtual screen
After=systemd-udev-settle.service systemd-modules-load.service
Before=display-manager.service

[Service]
Type=oneshot
ExecStart=/usr/local/sbin/display-autoselect.sh

[Install]
WantedBy=graphical.target
EOF

# ---- 5) x11vnc ----
if [ ! -f /etc/x11vnc.pass ]; then
  echo; echo ">> ตั้งรหัส VNC (พิมพ์ 2 ครั้ง แล้วตอบ y) — ใช้รหัสคนละตัวกับเครื่องอื่น"
  sudo x11vnc -storepasswd /etc/x11vnc.pass
fi
sudo tee /usr/local/bin/x11vnc-start.sh > /dev/null <<'EOF'
#!/bin/sh
# รอ Xorg ขึ้น -> หา auth + display เอง -> เปิด VNC 5900
while :; do
  ARGS=$(ps -o args= -C Xorg | head -1)
  AUTH=$(echo "$ARGS" | grep -o -- '-auth [^ ]*' | cut -d' ' -f2)
  DISP=$(echo "$ARGS" | grep -o ' :[0-9]*' | head -1 | tr -d ' ')
  [ -n "$AUTH" ] && [ -f "$AUTH" ] && break
  sleep 2
done
exec /usr/bin/x11vnc -display "${DISP:-:0}" -auth "$AUTH" -forever -shared -noshm -repeat \
  -wait 5 -defer 5 -nonap -rfbauth /etc/x11vnc.pass -rfbport 5900
EOF
sudo chmod +x /usr/local/bin/x11vnc-start.sh
sudo tee /etc/systemd/system/x11vnc.service > /dev/null <<'EOF'
[Unit]
Description=x11vnc
After=display-manager.service
Requires=display-manager.service

[Service]
ExecStart=/usr/local/bin/x11vnc-start.sh
Restart=always
RestartSec=3

[Install]
WantedBy=graphical.target
EOF

# ---- 6) เน็ตต่อใหม่เองเสมอ + net-watchdog ----
nmcli -t -f NAME,TYPE connection show | while IFS=: read -r NAME TYPE; do
  case "$TYPE" in
    802-3-ethernet|802-11-wireless)
      sudo nmcli connection modify "$NAME" connection.autoconnect yes connection.autoconnect-retries 0 || true ;;
  esac
done
sudo tee /usr/local/sbin/net-watchdog.sh > /dev/null <<'EOF'
#!/bin/sh
# ping ไม่ผ่าน 3 ครั้งติด -> สั่ง NetworkManager ต่อใหม่
for i in 1 2 3; do ping -c1 -W3 1.1.1.1 >/dev/null 2>&1 && exit 0; sleep 5; done
logger "net-watchdog: เน็ตหลุด -> รีสตาร์ต NetworkManager"
nmcli networking on
systemctl restart NetworkManager
EOF
sudo chmod +x /usr/local/sbin/net-watchdog.sh
sudo tee /etc/systemd/system/net-watchdog.service > /dev/null <<'EOF'
[Unit]
Description=Reconnect network if internet is down

[Service]
Type=oneshot
ExecStart=/usr/local/sbin/net-watchdog.sh
EOF
sudo tee /etc/systemd/system/net-watchdog.timer > /dev/null <<'EOF'
[Unit]
Description=Check internet every 2 minutes

[Timer]
OnBootSec=3min
OnUnitActiveSec=2min

[Install]
WantedBy=timers.target
EOF

# ---- 7) ปิด sleep / ล็อกจอ ----
sudo systemctl mask sleep.target suspend.target hibernate.target hybrid-sleep.target
gsettings set org.gnome.desktop.session idle-delay 0 2>/dev/null || true
gsettings set org.gnome.desktop.screensaver lock-enabled false 2>/dev/null || true
gsettings set org.gnome.settings-daemon.plugins.power sleep-inactive-ac-type 'nothing' 2>/dev/null || true

# ---- เปิด service ----
sudo systemctl daemon-reload
sudo systemctl enable display-autoselect x11vnc
sudo systemctl enable --now net-watchdog.timer

# ---- สรุป ----
echo
echo "== เสร็จ =="
grep -E '^(WaylandEnable|AutomaticLogin)' "$GDM"
for p in /sys/class/drm/card*-*; do echo "  จอ $(basename "$p"): $(cat "$p/status")"; done
sudo zerotier-cli listnetworks | tail -n +2 || true
echo "ZeroTier: ติ๊ก Auth ให้เครื่องนี้ที่ my.zerotier.com ถ้ายังไม่ได้ทำ  (Node ID: $(sudo zerotier-cli info | cut -d' ' -f3))"
echo "ต่อไป: sudo reboot  แล้วถอดจอได้เลย -> VNC ไปที่ IP ZeroTier พอร์ต 5900"
