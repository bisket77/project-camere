"""ดึงรายการกล้อง/โปรเจกเตอร์จาก Google Sheet (ผ่าน Apps Script web app) แล้วเพิ่มเข้าระบบอัตโนมัติ

ตั้งค่า (env): SHEET_URL = URL .../exec ของ Apps Script, SHEET_TOKEN = SYNC_TOKEN, SHEET_POLL_SEC (ค่าเริ่ม 30)
- เพิ่มเฉพาะเครื่องที่ IP ยังไม่มีในระบบ (ไม่ลบ/ไม่ทับของเดิม — แก้ username/password ในเว็บ ไม่ใช่ในชีต)
- เก็บค่าในชีตล่าสุดไว้ (lookup) ให้หน้าแก้ไขในเว็บแสดง "ค่าใน Google Sheet" เทียบได้
- ห้องตามคอลัมน์ Room / Location(อาคาร) / ชั้น ถ้ายังไม่มีจะสร้างให้
- เพิ่มไม่สำเร็จ (ติดต่อไม่ได้) จะลองใหม่รอบถัดไป แต่รหัสผิดเว้น 10 นาที กันกล้องล็อกบัญชี
"""
import os
import re
import threading
import time
import uuid

import requests

AUTH_WAIT_SEC = 600

# รุ่นกล้อง (คอลัมน์ Type ใน Sheet) -> วิธีเชื่อมต่อ  — ตรวจตามลำดับ ตัวแรกที่เจอคำในชื่อรุ่นชนะ
#   brand dahua / hikvision = ใช้ระบบเดิม (เช็กรหัสผ่าน + RTSP path ของยี่ห้อ)
#   brand other             = "อื่น ๆ (ใส่ RTSP URL เอง)" สร้าง URL ให้จาก path ด้านล่าง (ใส่ user:pass ให้เอง)
#   path None + brand other = สร้างเองไม่ได้ ต้องใส่ URL ในคอลัมน์ "RTSP URL" ของ Sheet
CAMERA_MODELS = [
    (("DH-", "DAHUA"), "dahua", None),
    (("DS-2CD", "HIKVISION", "HIK"), "hikvision", None),
    # Compro IP540/IP570(P): ทดสอบกับ 192.168.11.108 แล้ว — /medias1 = H.264 1280x1024 (มี /medias2 ถ้าเปิดสตรีม 2)
    (("IP570", "IP540", "COMPRO"), "other", "/medias1"),
    (("WV-", "PANASONIC", "I-PRO"), "other", "/MediaInput/h264"),
    (("UNIFI", "UVC", "G3", "G4", "G5"), "other", None),
]
# สั่งซิงก์ทันที (ไม่ต้องรอรอบ 30 วิ และดึงทั้งชีตใหม่): touch web/data/.sync_now — ใช้กับ tools/nuc/sync_sheet.sh
SYNC_NOW_FLAG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", ".sync_now")


def _int(v, default):
    try:
        return int(str(v).strip())
    except ValueError:
        return default


class SheetSync:
    def __init__(self, app_module, url, token, poll_sec=30, flag_path=SYNC_NOW_FLAG):
        self.m, self.url, self.token, self.poll_sec = app_module, url, token, poll_sec
        self.flag_path = flag_path
        self.version = ""
        self.rows = []
        self.wait_until = {}  # ip -> เวลาที่ลองใหม่ได้
        self.last_error = {}

    def start(self):
        threading.Thread(target=self._loop, daemon=True).start()

    def _loop(self):
        while True:
            try:
                self.poll()
            except Exception as e:  # เน็ตหลุด ฯลฯ ลองรอบหน้า
                print(f"[sheet] ดึงข้อมูลไม่ได้: {e}")
            self.wait_next()

    def wait_next(self):
        """รอรอบถัดไป แต่ถ้ามีไฟล์สั่งซิงก์ทันที -> ออกเลย และล้าง version ให้ดึงทั้งชีตใหม่"""
        end = time.time() + self.poll_sec
        while time.time() < end:
            if self.take_sync_flag():
                return
            time.sleep(1)

    def take_sync_flag(self):
        if not os.path.exists(self.flag_path):
            return False
        try:
            os.remove(self.flag_path)
        except OSError:
            pass
        self.version = ""
        self.wait_until.clear()  # ลองเครื่องที่รอ (รหัสผิด) ใหม่ด้วย
        print("[sheet] สั่งซิงก์ทันที (manual)")
        return True

    def poll(self):
        r = requests.get(self.url, params={"token": self.token, "v": self.version}, timeout=20)
        data = r.json()
        if data.get("error"):
            raise RuntimeError(data["error"])
        if not data.get("unchanged"):
            self.rows = data.get("rows") or []
            self.version = str(data.get("version", ""))
            print(f"[sheet] ได้ข้อมูลใหม่ {len(self.rows)} แถว")
        self.apply(self.rows)  # ทำทุกรอบ: แถวที่รอบก่อนเพิ่มไม่สำเร็จจะได้ลองใหม่

    def room_id(self, row):
        name = (row.get("room") or "").strip()
        if not name:
            return None
        building, floor = (row.get("building") or "").strip(), (row.get("floor") or "").strip()
        for r in self.m.db.list_rooms():
            if r["name"] == name and (r.get("building") or "") == building:
                return r["id"]
        room = {"id": uuid.uuid4().hex[:8], "name": name[:60], "building": building[:80],
                "floor": floor[:20], "note": "จาก Google Sheet"}
        self.m.db.save_room(room)
        self.m.db.save_room_building(room)
        print(f"[sheet] สร้างห้อง {building} / {name}")
        return room["id"]

    def apply(self, rows):
        m = self.m
        with m.cameras_lock:
            cam_ips = {c.ip for c in m.cameras.values()}
            # URL ที่ใส่เอง (เช่น UniFi ชี้ไป IP ของ console ไม่ใช่ IP กล้อง) -> เทียบด้วย URL แทน
            cam_urls = {_strip_auth(getattr(c, "rtsp_input", "") or "") for c in m.cameras.values()} - {""}
        with m.projectors_lock:
            proj_ips = {p.ip for p in m.projectors.values()}
        for row in rows:
            ip, kind = row.get("ip", ""), (row.get("type") or "").lower()
            is_proj = "projector" in kind
            if ip in (proj_ips if is_proj else cam_ips) or time.time() < self.wait_until.get(ip, 0):
                continue
            if not is_proj and _strip_auth(row.get("rtsp") or "") in cam_urls:
                continue
            name = " ".join(x for x in (row.get("room"), row.get("type"), row.get("no")) if x)
            try:
                rid = self.room_id(row)
                if is_proj:
                    self.add_projector(row, name, rid)
                else:
                    self.add_camera(row, name, rid)
                print(f"[sheet] เพิ่ม {name} ({ip}) แล้ว")
                self.last_error.pop(ip, None)
            except m.FormError as e:
                msg = str(e)
                if "password" in msg:
                    self.wait_until[ip] = time.time() + AUTH_WAIT_SEC
                if self.last_error.get(ip) != msg:  # ไม่พิมพ์ข้อความเดิมซ้ำทุก 30 วิ
                    print(f"[sheet] เพิ่ม {name} ({ip}) ไม่ได้: {msg}")
                    self.last_error[ip] = msg

    def lookup(self, kind, ips):
        """ค่าในชีตของเครื่องนี้ (kind = "cameras" / "projectors", ips = IP ที่อาจตรง) -> dict หรือ None"""
        want_proj = kind == "projectors"
        for row in self.rows:
            if ("projector" in (row.get("type") or "").lower()) == want_proj and row.get("ip") in ips:
                return {k: row.get(k, "") for k in ("no", "type", "model", "ip", "user", "password", "port",
                                                     "pjlink_port", "control_port", "building", "room", "floor",
                                                     "rtsp")}
        return None

    def add_camera(self, row, name, room_id):
        m = self.m
        brand, rtsp_url = camera_plan(row)
        if brand == "other" and not rtsp_url:
            raise m.FormError(f"รุ่น {row.get('model') or '?'} สร้าง RTSP URL เองไม่ได้ — "
                              "ใส่ URL ในคอลัมน์ 'RTSP URL' ของ Sheet (UniFi: คัดลอกจาก UniFi Protect)")
        cam = m.build_camera({"name": name, "ip": row["ip"], "user": row.get("user", ""),
                              "password": row.get("password", ""), "brand": brand, "rtsp_url": rtsp_url,
                              "rtsp_port": _int(row.get("port"), 554), "room_id": room_id})
        with m.cameras_lock:
            m.cameras[cam.id] = cam
        m.db.save_camera(cam)

    def add_projector(self, row, name, room_id):
        m = self.m
        p = m.build_projector({"name": name, "ip": row["ip"], "user": row.get("user", ""),
                               "password": row.get("password", ""),
                               "pjlink_port": _int(row.get("pjlink_port"), 4352),
                               "control_port": _int(row.get("control_port"), 1024), "room_id": room_id})
        with m.projectors_lock:
            m.projectors[p.id] = p
        m.db.save_projector(p)


def _strip_auth(url):
    """rtsp://user:pass@host/x -> rtsp://host/x (เทียบ URL โดยไม่สนรหัส)"""
    return re.sub(r"//[^/@]*@", "//", url.strip())


def camera_plan(row):
    """เลือกยี่ห้อ + RTSP URL จากแถวใน Sheet -> (brand, rtsp_url)"""
    custom = (row.get("rtsp") or "").strip()
    if custom:  # ใส่ URL เองในคอลัมน์ RTSP URL -> ใช้ตัวนั้นเสมอ
        return "other", custom
    model = (row.get("model") or "").upper()
    ip, port = row.get("ip", ""), _int(row.get("port"), 554)
    for words, brand, path in CAMERA_MODELS:
        if any(w in model for w in words):
            return brand, (f"rtsp://{ip}:{port}{path}" if path else "")
    return "dahua", ""  # รุ่นที่ไม่รู้จัก: ลองแบบ Dahua (ค่าเดิม)


def start_from_env(app_module):
    url, token = os.environ.get("SHEET_URL"), os.environ.get("SHEET_TOKEN")
    if not url or not token:
        return None
    s = SheetSync(app_module, url, token, _int(os.environ.get("SHEET_POLL_SEC", "30"), 30))
    app_module.SHEET = s  # ให้ API /api/<kind>/<id>/sheet อ่านค่าในชีตได้
    s.start()
    print("[sheet] เปิดซิงก์กับ Google Sheet")
    return s
