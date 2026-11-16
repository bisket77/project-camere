#!/bin/bash
# ตั้ง DNS ในวง ZeroTier B ให้เปิดเว็บด้วยชื่อได้ — รันบน NUC-02 ครั้งเดียว:
#   bash setup_dns_b.sh              # ชื่อ cctv.sut
#   bash setup_dns_b.sh <ชื่ออื่น>   # เช่น cctv2.sut
#
# ทำอะไร: ลง dnsmasq ให้ตอบคำถาม DNS เฉพาะบนการ์ด ZeroTier วง B
#   <ชื่อ>        -> IP ZeroTier ของเครื่องนี้
#   ชื่ออื่นทั้งหมด -> ส่งต่อไป 1.1.1.1 / 8.8.8.8
# แล้วไปตั้งที่ my.zerotier.com วง B -> DNS: Search Domain = <ชื่อ>, Server Address = IP ที่สคริปต์บอก
set -e
DOMAIN="${1:-cctv.sut}"
NET=b6079f73c6de1256

# หาการ์ด + IPv4 ของวง B
read -r IFACE IP < <(sudo zerotier-cli -j listnetworks | python3 -c "
import sys, json
for n in json.load(sys.stdin):
    if n['nwid'] == '$NET':
        v4 = [a.split('/')[0] for a in n['assignedAddresses'] if ':' not in a]
        print(n['portDeviceName'], v4[0] if v4 else '')
")
[ -n "$IP" ] || { echo "ยังไม่ได้ IP ในวง B — เช็ก: sudo zerotier-cli listnetworks (ต้อง OK) และติ๊ก Auth"; exit 1; }
echo "วง B: การ์ด $IFACE  IP $IP  ชื่อ $DOMAIN"

sudo apt install -y dnsmasq || true   # ครั้งแรกอาจขึ้น error ชนพอร์ต 53 — ตั้งค่าด้านล่างแล้วจะหาย

# ฟังเฉพาะการ์ด ZeroTier (ไม่ชนกับ systemd-resolved ที่ 127.0.0.53)
sudo tee /etc/dnsmasq.d/zt-b.conf > /dev/null <<EOF
interface=$IFACE
except-interface=lo
bind-dynamic
no-resolv
server=1.1.1.1
server=8.8.8.8
address=/$DOMAIN/$IP
EOF
# ให้ dnsmasq เปิดหลัง ZeroTier (การ์ดต้องมีก่อน)
sudo mkdir -p /etc/systemd/system/dnsmasq.service.d
sudo tee /etc/systemd/system/dnsmasq.service.d/after-zerotier.conf > /dev/null <<'EOF'
[Unit]
After=zerotier-one.service
Wants=zerotier-one.service

[Service]
Restart=on-failure
RestartSec=5
EOF
sudo systemctl daemon-reload
sudo systemctl enable dnsmasq
sudo systemctl restart dnsmasq
if sudo ufw status | grep -q "Status: active"; then sudo ufw allow in on "$IFACE" to any port 53; fi

# ---- ตรวจ ----
sleep 1
echo
systemctl is-active --quiet dnsmasq && echo "✓ dnsmasq ทำงาน" || { echo "✗ dnsmasq ไม่ทำงาน"; journalctl -u dnsmasq -n 15 --no-pager; exit 1; }
nslookup "$DOMAIN" "$IP" 2>/dev/null | tail -2 || true
echo
echo "ขั้นต่อไป (my.zerotier.com -> วง B -> Settings -> DNS):"
echo "   Search Domain  = $DOMAIN"
echo "   Server Address = $IP"
echo "เครื่องลูกข่าย Windows: คลิกขวาไอคอน ZeroTier -> วง B -> ติ๊ก Allow DNS Configuration"
echo "แล้วเปิด http://$DOMAIN"
