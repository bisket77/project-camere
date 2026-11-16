#!/bin/bash
# ดึงกล้อง/โปรเจกเตอร์จาก Google Sheet ทันที (Manual) — ไม่ต้องรอรอบ 30 วิ และไม่ต้องรีสตาร์ตเว็บ
#   ดับเบิลคลิกไอคอน "Sync Sheet" บน Desktop  หรือ  bash ~/Desktop/project-camere/tools/nuc/sync_sheet.sh
APP="$(cd "$(dirname "$(readlink -f "$0")")/../.." && pwd)"
pause() { echo; read -r -p "กด Enter เพื่อปิดหน้าต่าง " _; }

echo "== Sync Google Sheet (Manual) =="
if ! systemctl is-active --quiet classroom-web; then
  echo "✗ เว็บไม่ได้รันอยู่ — เปิดก่อน: sudo systemctl start classroom-web"
  pause; exit 1
fi
if ! grep -q '^SHEET_URL=.\+' "$APP/web/.env" 2>/dev/null || ! grep -q '^SHEET_TOKEN=.\+' "$APP/web/.env"; then
  echo "✗ ยังไม่ได้ตั้ง SHEET_URL / SHEET_TOKEN ใน $APP/web/.env"
  echo "  แก้: nano $APP/web/.env  แล้ว sudo systemctl restart classroom-web"
  pause; exit 1
fi

mkdir -p "$APP/web/data"
touch "$APP/web/data/.sync_now"
echo "สั่งแล้ว — เว็บจะดึงทั้งชีตใหม่ภายใน 1-2 วินาที"
echo "---- log การซิงก์ (แสดง 30 วินาที) ----"
timeout 30 journalctl -u classroom-web -f -n 0 -o cat 2>/dev/null | grep --line-buffered '\[sheet\]'
if [ -f "$APP/web/data/.sync_now" ]; then
  echo "⚠ เว็บยังไม่รับคำสั่ง (เว็บเวอร์ชันเก่า?) — deploy โค้ดใหม่จาก PC หรือ sudo systemctl restart classroom-web"
else
  echo "✓ ซิงก์เสร็จ — ถ้าไม่เห็น log ด้านบน: ออกจากระบบแล้วเข้าใหม่ 1 ครั้ง (สิทธิ์อ่าน log)"
fi
pause
