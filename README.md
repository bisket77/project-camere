# project-camere — เว็บดูกล้อง IP + ควบคุมโปรเจกเตอร์ แบบเรียลไทม์

![CI](https://github.com/bisket77/project-camere/actions/workflows/ci.yml/badge.svg)
![Deploy](https://github.com/bisket77/project-camere/actions/workflows/pages.yml/badge.svg)

**เปิดดูหน้าเว็บ (เดโม):** https://bisket77.github.io/project-camere/
ข้อมูลและภาพกล้องในเดโมเป็นของจำลอง (GitHub Pages รัน Python ไม่ได้ และมองไม่เห็นกล้องใน LAN)
ใช้งานจริงให้รัน `python web/app.py` บนเครื่องที่อยู่วงเดียวกับกล้อง

ส่วนหนึ่งของโครงการระบบดูกล้องวงจรปิดและควบคุมโปรเจกเตอร์ (ศูนย์บรรณสารฯ)
จัดอุปกรณ์ตามอาคาร/ห้อง (เช่น อาคาร B1 ห้อง B1121) แต่ละห้องมีกล้องและโปรเจกเตอร์กี่ตัวก็ได้
เพิ่มด้วย IP + username + password แล้วดูภาพสด สถานะ และสั่งเปิด/ปิดโปรเจกเตอร์ได้จากหน้าเว็บเดียว
ไม่บันทึกภาพหรือวิดีโอ เก็บเฉพาะข้อมูลอุปกรณ์และสถานะล่าสุด

## ความสามารถ

### ห้อง / อาคาร
- เพิ่ม / แก้ไข / ลบห้อง จัดกลุ่มตามอาคาร กรองดูทีละอาคาร
- การ์ดห้องแสดงภาพกล้องทุกตัว + สถานะโปรเจกเตอร์ทุกเครื่องในห้องนั้น ปุ่ม ON / STANDBY ได้ทันที
- ลบห้องแล้วอุปกรณ์ไม่หาย แค่ย้ายไป "ยังไม่ระบุห้อง"

### โปรเจกเตอร์ (ทดสอบรูปแบบ Panasonic PT-VX420)
- สถานะแบบหน้า Projector Control Window: รุ่น, Serial, Power, Input, AV Mute, Picture mode,
  อุณหภูมิ (Intake / Exhaust / Optics), ชั่วโมงหลอดไฟ, Self test
- ควบคุม: Power ON/STANDBY, Input, Volume, Picture mode, AV Mute
- ใช้ PJLink (พอร์ต 4352) + Panasonic NTCONTROL (พอร์ต 1024) — ที่ตัวเครื่องต้องเปิด Network control และ PJLink
- ถามสถานะทุก 5 วินาที เก็บสถานะล่าสุดและชั่วโมงหลอดไฟลง DB

### กล้อง

- เพิ่ม / แก้ไข / ลบกล้อง ตรวจการเชื่อมต่อและรหัสผ่านก่อนบันทึก
- ตารางสถานะพอดีจอ: ออนไลน์/ออฟไลน์, ping, ความละเอียด, FPS, รุ่น, Serial, Firmware, ออนไลน์ล่าสุด
- ดูภาพสดได้ 3 ช่องทาง ระบบเลือกให้อัตโนมัติ
  1. RTSP → ส่ง H.264 ให้เบราว์เซอร์ตรง ๆ (ลื่นสุด ~25 fps)
  2. MJPEG ผ่าน HTTP
  3. ภาพนิ่งต่อเนื่องผ่าน HTTP (ขอพร้อมกันหลายเส้น)
- รองรับกล้องที่เปิด RTSP over TLS (rtsps) ด้วยตัวกลาง TLS ในตัว
- ปุ่มเชื่อมต่อใหม่ทันที (รายกล้อง / ทั้งหมด)
- ฐานข้อมูล SQLite เก็บรายการกล้อง รีสตาร์ทแล้วกลับมาเอง รหัสผ่านเข้ารหัส (Fernet/AES)
- รองรับ Dahua และ Hikvision (ดึงข้อมูลเครื่องได้) และยี่ห้ออื่นผ่าน RTSP URL

## เริ่มใช้งาน

```bash
pip install -r requirements.txt
python web/app.py                  # เปิด http://127.0.0.1:5000
python web/app.py --host 0.0.0.0   # ให้เครื่องอื่นในวง LAN / ZeroTier เปิดได้
```

หน้าเว็บยังไม่มีระบบล็อกอิน ถ้าเปิดด้วย `--host 0.0.0.0` ทุกคนในเครือข่ายเดียวกันจะดูกล้องได้

## โครงสร้าง

```
web/
  app.py            เซิร์ฟเวอร์ Flask: เชื่อมกล้อง, API ห้อง/กล้อง/โปรเจกเตอร์, สตรีมภาพ
  projector.py      คุยกับโปรเจกเตอร์ (PJLink + Panasonic NTCONTROL)
  db.py             ฐานข้อมูล ห้อง / กล้อง / โปรเจกเตอร์ (SQLite) + เข้ารหัสรหัสผ่าน
  static/index.html หน้าเว็บ
  data/             ฐานข้อมูลและกุญแจ (สร้างอัตโนมัติ ไม่ขึ้น git)
tools/
  test_camera.py    ทดสอบดึงภาพจากกล้องหนึ่งตัว
  view_camera.py    ดูภาพสดในหน้าต่าง OpenCV (ต้องใช้ opencv-python แบบมี GUI)
tests/              ชุดทดสอบอัตโนมัติ (ไม่ต้องมีกล้อง/โปรเจกเตอร์จริง มีโปรเจกเตอร์จำลอง fake_projector.py)
.github/workflows/  GitHub Actions
```

## ทดสอบ

```bash
pip install -r requirements-dev.txt
ruff check web tools tests
python -m pytest -v
```

GitHub Actions รันทุกครั้งที่ push บน Linux และ Windows:
ตรวจว่าไม่มีไฟล์ลับหลุด → ruff → compile → pytest

## ความลับ

- ห้าม commit โฟลเดอร์ `web/data/` (มี `cameras.db` และ `secret.key`) — `.gitignore` กันไว้แล้ว
- `secret.key` คือกุญแจถอดรหัสรหัสผ่านกล้อง ถ้าหาย ต้องใส่รหัสผ่านกล้องใหม่ผ่านปุ่ม "แก้ไข"
- ตั้งกุญแจผ่านตัวแปร `CAMERA_SECRET_KEY` แทนไฟล์ได้
- repo นี้เป็น public: ห้ามใส่เอกสารลูกค้า ไฟล์เสียงสัมภาษณ์ รูปหน้างาน หรือรหัสผ่านใด ๆ
