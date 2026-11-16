#!/bin/bash
# ตั้งเว็บ Classroom Monitor ให้รันอัตโนมัติบน NUC (Ubuntu) — รันครั้งเดียว:
#   bash ~/Desktop/project-camere/tools/nuc/setup_autostart.sh
#
# สิ่งที่ตั้ง
#   1) classroom-web.service : เปิดเว็บเองตอนบูต รอเน็ตก่อน (สูงสุด 2 นาที) ล่มแล้วเปิดใหม่ใน 5 วิ
#   2) nginx                 : เข้า http://bis.lab / http://<IP> ได้โดยไม่ต้องใส่ :5000
#   3) หน้าต่าง log          : ล็อกอินเข้า desktop แล้วเด้ง Terminal แสดง log เว็บ
set -e
APP="$(cd "$(dirname "$0")/../.." && pwd)"
USER_NAME="$(whoami)"
echo "โฟลเดอร์โปรเจกต์: $APP  (ผู้ใช้ $USER_NAME)"

# ---- venv + .env ----
cd "$APP"
[ -d .venv ] || python3 -m venv .venv
.venv/bin/pip install -q -r requirements.txt
[ -f web/.env ] || { touch web/.env; chmod 600 web/.env; echo "สร้าง web/.env ว่าง (ใส่ SHEET_URL / SHEET_TOKEN ถ้าจะซิงก์ Sheet)"; }

# ---- 1) service ----
sudo tee /etc/systemd/system/classroom-web.service > /dev/null <<UNIT
[Unit]
Description=Classroom Monitor web
After=network-online.target
Wants=network-online.target
StartLimitIntervalSec=0

[Service]
User=$USER_NAME
WorkingDirectory=$APP
EnvironmentFile=-$APP/web/.env
ExecStartPre=-/usr/bin/timeout 120 /bin/sh -c 'until ping -c1 -W2 1.1.1.1 >/dev/null 2>&1; do sleep 2; done'
ExecStart=$APP/.venv/bin/python web/app.py --host 0.0.0.0 --port 5000
Restart=always
RestartSec=5
TimeoutStartSec=180

[Install]
WantedBy=multi-user.target
UNIT
sudo systemctl daemon-reload
sudo systemctl enable classroom-web
sudo systemctl restart classroom-web

# ---- 2) nginx (พอร์ต 80 -> 5000) ----
if ! command -v nginx >/dev/null; then sudo apt install -y nginx; fi
sudo tee /etc/nginx/sites-available/bis > /dev/null <<'NGX'
server {
    listen 80 default_server;
    server_name bis.lab _;
    location / {
        proxy_pass http://127.0.0.1:5000;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_buffering off;
        proxy_read_timeout 3600s;
    }
}
NGX
sudo ln -sf /etc/nginx/sites-available/bis /etc/nginx/sites-enabled/bis
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t && sudo systemctl enable --now nginx && sudo systemctl reload nginx

# ---- 3) หน้าต่าง log ตอนล็อกอิน ----
sudo usermod -aG systemd-journal "$USER_NAME"
mkdir -p ~/.local/bin ~/.config/autostart
cat > ~/.local/bin/web-console.sh <<'SH'
#!/bin/sh
echo "รอเน็ต..."
until ping -c1 -W2 1.1.1.1 >/dev/null 2>&1; do sleep 2; done
IP=$(hostname -I | tr ' ' '\n' | grep -E '^(192\.168\.191|172\.28)\.' | head -1)
echo "มีเน็ตแล้ว $(date '+%H:%M:%S')   เว็บ: http://$IP   (ชื่อ: bis.lab วง A / cctv.sut วง B)"
echo "---- log เว็บ (ปิดหน้าต่างนี้ได้ เว็บยังทำงานต่อ) ----"
exec journalctl -u classroom-web -f -n 30
SH
chmod +x ~/.local/bin/web-console.sh
cat > ~/.config/autostart/web-console.desktop <<DESK
[Desktop Entry]
Type=Application
Name=Classroom web console
Exec=gnome-terminal --title="Classroom web" -- $HOME/.local/bin/web-console.sh
X-GNOME-Autostart-enabled=true
X-GNOME-Autostart-Delay=5
DESK

# ---- ตรวจ ----
sleep 4
echo
systemctl is-active --quiet classroom-web && echo "✓ classroom-web ทำงาน" || { echo "✗ classroom-web ไม่ทำงาน"; journalctl -u classroom-web -n 20 --no-pager; }
systemctl is-active --quiet nginx && echo "✓ nginx ทำงาน" || echo "✗ nginx ไม่ทำงาน"
curl -s -o /dev/null -w "✓ http://127.0.0.1 ตอบ %{http_code}\n" http://127.0.0.1 || true
echo "เสร็จ — รีบูตเพื่อทดสอบได้: sudo reboot"
