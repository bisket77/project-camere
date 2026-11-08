"""ฐานข้อมูล (SQLite ไฟล์เดียว: web/data/cameras.db)

เก็บ: ห้อง/อาคาร, กล้อง และโปรเจกเตอร์ (IP, พอร์ต, username, password) + สถานะล่าสุด + ข้อมูลเครื่อง
- password และ RTSP URL ที่ใส่เอง (อาจมีรหัสฝังอยู่) ถูกเข้ารหัสด้วย Fernet (AES) ก่อนบันทึก
- กุญแจเข้ารหัสอยู่แยกจากฐานข้อมูล: ตัวแปรสภาพแวดล้อม CAMERA_SECRET_KEY หรือไฟล์ web/data/secret.key
  (สร้างให้อัตโนมัติครั้งแรก) → ถ้าได้ไฟล์ .db ไปอย่างเดียวจะอ่านรหัสผ่านไม่ได้
- ห้ามนำไฟล์ในโฟลเดอร์ data/ ขึ้น git (มี .gitignore กันไว้แล้ว)
- ไม่เก็บภาพ/วิดีโอใด ๆ
"""
import os
import sqlite3
import threading
import time

from cryptography.fernet import Fernet, InvalidToken

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
DB_PATH = os.environ.get("CAMERA_DB_PATH") or os.path.join(DATA_DIR, "cameras.db")
KEY_PATH = os.path.join(DATA_DIR, "secret.key")

SCHEMA = """
CREATE TABLE IF NOT EXISTS cameras (
    id              TEXT PRIMARY KEY,          -- รหัสกล้อง (สุ่ม 8 ตัว)
    name            TEXT NOT NULL,             -- ชื่อที่ตั้ง เช่น ห้อง B1118
    brand           TEXT NOT NULL,             -- dahua / hikvision / other
    ip              TEXT NOT NULL,             -- IP ของกล้อง
    http_port       INTEGER NOT NULL DEFAULT 80,
    rtsp_port       INTEGER NOT NULL DEFAULT 554,
    quality         TEXT NOT NULL DEFAULT 'sub',  -- main / sub
    username        TEXT NOT NULL DEFAULT '',
    password_enc    TEXT NOT NULL DEFAULT '',     -- รหัสผ่าน (เข้ารหัสแล้ว)
    rtsp_url_enc    TEXT NOT NULL DEFAULT '',     -- RTSP URL ที่ใส่เอง (เข้ารหัสแล้ว)
    model           TEXT NOT NULL DEFAULT '',     -- รุ่น เช่น DH-IPC-HDBW1230R
    serial          TEXT NOT NULL DEFAULT '',
    firmware        TEXT NOT NULL DEFAULT '',
    device_name     TEXT NOT NULL DEFAULT '',
    last_status     TEXT NOT NULL DEFAULT '',     -- สถานะล่าสุด online / offline / ...
    last_error      TEXT NOT NULL DEFAULT '',
    last_mode       TEXT NOT NULL DEFAULT '',     -- rtsp / mjpeg / snapshot
    rtsp_tls        INTEGER NOT NULL DEFAULT 0,   -- 1 = กล้องใช้ RTSP แบบเข้ารหัส (rtsps)
    last_online_at  REAL,                         -- เวลาที่ออนไลน์ครั้งล่าสุด (unix time)
    created_at      REAL NOT NULL,
    updated_at      REAL NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_cameras_ip
    ON cameras(ip) WHERE brand != 'other';    -- IP ซ้ำไม่ได้ (ยกเว้นแบบใส่ URL เอง)

CREATE TABLE IF NOT EXISTS buildings (
    name        TEXT PRIMARY KEY,              -- ชื่ออาคาร (โฟลเดอร์) สร้างไว้ก่อนได้แม้ยังไม่มีห้อง
    sort_order  INTEGER NOT NULL DEFAULT 0,    -- ลำดับที่จัดเอง (0 = ยังไม่จัด ต่อท้าย)
    created_at  REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS rooms (
    id          TEXT PRIMARY KEY,
    building    TEXT NOT NULL DEFAULT '',      -- อาคาร เช่น B1 อาคารเรียนรวม 1
    floor       TEXT NOT NULL DEFAULT '',      -- ชั้น เช่น 2
    name        TEXT NOT NULL,                 -- ห้อง เช่น B1121
    note        TEXT NOT NULL DEFAULT '',
    created_at  REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS projectors (
    id              TEXT PRIMARY KEY,
    room_id         TEXT REFERENCES rooms(id) ON DELETE SET NULL,
    name            TEXT NOT NULL,
    ip              TEXT NOT NULL UNIQUE,
    pjlink_port     INTEGER NOT NULL DEFAULT 4352,  -- PJLink (มาตรฐานกลาง)
    control_port    INTEGER NOT NULL DEFAULT 1024,  -- Panasonic NTCONTROL
    username        TEXT NOT NULL DEFAULT '',
    password_enc    TEXT NOT NULL DEFAULT '',       -- รหัสผ่าน (เข้ารหัสแล้ว)
    model           TEXT NOT NULL DEFAULT '',
    serial          TEXT NOT NULL DEFAULT '',
    projector_name  TEXT NOT NULL DEFAULT '',
    last_status     TEXT NOT NULL DEFAULT '',
    last_error      TEXT NOT NULL DEFAULT '',
    lamp_hours      INTEGER,
    last_online_at  REAL,
    created_at      REAL NOT NULL,
    updated_at      REAL NOT NULL
);
"""

_lock = threading.Lock()
_conn = None
_fernet = None


def _load_key():
    key = os.environ.get("CAMERA_SECRET_KEY")
    if key:
        return key.encode()
    if os.path.exists(KEY_PATH):
        with open(KEY_PATH, "rb") as f:
            return f.read().strip()
    key = Fernet.generate_key()
    with open(KEY_PATH, "wb") as f:
        f.write(key)
    try:
        os.chmod(KEY_PATH, 0o600)
    except OSError:
        pass
    return key


def init():
    """เปิด/สร้างฐานข้อมูล เรียกครั้งเดียวตอนเริ่มโปรแกรม"""
    global _conn, _fernet
    os.makedirs(DATA_DIR, exist_ok=True)
    _fernet = Fernet(_load_key())
    _conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    _conn.row_factory = sqlite3.Row
    _conn.execute("PRAGMA journal_mode=WAL")
    _conn.executescript(SCHEMA)
    # ฐานข้อมูลรุ่นเก่ายังไม่มีคอลัมน์ห้องของกล้อง → เพิ่มให้ (ข้อมูลเดิมไม่หาย)
    cols = {r[1] for r in _conn.execute("PRAGMA table_info(cameras)")}
    if "room_id" not in cols:
        _conn.execute("ALTER TABLE cameras ADD COLUMN room_id TEXT")
    if "floor" not in {r[1] for r in _conn.execute("PRAGMA table_info(rooms)")}:
        _conn.execute("ALTER TABLE rooms ADD COLUMN floor TEXT NOT NULL DEFAULT ''")
    # ลำดับที่ผู้ใช้จัดเอง (โหมดแก้ไข ปุ่มขึ้น/ลง)
    for table in ("rooms", "cameras", "projectors"):
        if "sort_order" not in {r[1] for r in _conn.execute(f"PRAGMA table_info({table})")}:
            _conn.execute(f"ALTER TABLE {table} ADD COLUMN sort_order INTEGER NOT NULL DEFAULT 0")
    # อาคารที่มีอยู่ในห้องแล้ว (ฐานข้อมูลรุ่นก่อน) -> สร้างโฟลเดอร์อาคารให้
    _conn.execute("INSERT OR IGNORE INTO buildings (name, created_at) "
                  "SELECT DISTINCT building, ? FROM rooms WHERE building != ''", (time.time(),))
    _conn.commit()


def encrypt(text):
    return _fernet.encrypt(text.encode()).decode() if text else ""


def decrypt(token):
    if not token:
        return ""
    try:
        return _fernet.decrypt(token.encode()).decode()
    except InvalidToken:
        # กุญแจเปลี่ยน/หาย → อ่านรหัสเดิมไม่ได้ ผู้ใช้ต้องกด "แก้ไข" แล้วใส่รหัสใหม่
        return ""


def load_all():
    with _lock:
        rows = _conn.execute("SELECT * FROM cameras ORDER BY created_at").fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["password"] = decrypt(d.pop("password_enc"))
        d["rtsp_url"] = decrypt(d.pop("rtsp_url_enc"))
        out.append(d)
    return out


def save_camera(cam):
    """เพิ่มหรือแก้ไขกล้อง (ตอนกด เพิ่ม / แก้ไข)"""
    if _conn is None:  # ยังไม่ได้ init (เช่น ตอนทดสอบบางส่วน) → ไม่บันทึก
        return
    now = time.time()
    info = cam.info or {}
    with _lock:
        _conn.execute(
            """INSERT INTO cameras (id, name, brand, ip, http_port, rtsp_port, quality, username,
                   password_enc, rtsp_url_enc, model, serial, firmware, device_name, created_at, updated_at,
                   room_id)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(id) DO UPDATE SET
                   name=excluded.name, room_id=excluded.room_id, brand=excluded.brand, ip=excluded.ip,
                   http_port=excluded.http_port, rtsp_port=excluded.rtsp_port, quality=excluded.quality,
                   username=excluded.username, password_enc=excluded.password_enc,
                   rtsp_url_enc=excluded.rtsp_url_enc,
                   model=CASE WHEN excluded.model != '' THEN excluded.model ELSE cameras.model END,
                   serial=CASE WHEN excluded.serial != '' THEN excluded.serial ELSE cameras.serial END,
                   firmware=CASE WHEN excluded.firmware != '' THEN excluded.firmware ELSE cameras.firmware END,
                   device_name=CASE WHEN excluded.device_name != '' THEN excluded.device_name
                                    ELSE cameras.device_name END,
                   updated_at=excluded.updated_at""",
            (cam.id, cam.name, cam.brand, cam.ip, cam.http_port, cam.rtsp_port, cam.quality, cam.user,
             encrypt(cam.password), encrypt(cam.custom_rtsp), info.get("model", ""), info.get("serial", ""),
             info.get("firmware", ""), info.get("device_name", ""), now, now, getattr(cam, "room_id", None)))
        _conn.commit()


def update_runtime(cam_id, **fields):
    """บันทึกสถานะล่าสุด / ข้อมูลเครื่อง (เรียกเฉพาะตอนค่าเปลี่ยน ไม่ได้เขียนทุกวินาที)"""
    allowed = {"last_status", "last_error", "last_mode", "rtsp_tls", "last_online_at",
               "model", "serial", "firmware", "device_name"}
    fields = {k: v for k, v in fields.items() if k in allowed}
    if not fields or _conn is None:
        return
    cols = ", ".join(f"{k}=?" for k in fields)
    with _lock:
        _conn.execute(f"UPDATE cameras SET {cols} WHERE id=?", (*fields.values(), cam_id))
        _conn.commit()


def delete_camera(cam_id):
    if _conn is None:
        return
    with _lock:
        _conn.execute("DELETE FROM cameras WHERE id=?", (cam_id,))
        _conn.commit()


# ---------------------- ห้อง / อาคาร ----------------------
def list_rooms():
    if _conn is None:
        return []
    with _lock:
        rows = _conn.execute("SELECT * FROM rooms ORDER BY building, floor, name").fetchall()
    return [dict(r) for r in rows]


def save_room(room):
    """room = {"id", "building", "name", "note"} เพิ่มหรือแก้ไข"""
    with _lock:
        _conn.execute(
            """INSERT INTO rooms (id, building, floor, name, note, created_at) VALUES (?,?,?,?,?,?)
               ON CONFLICT(id) DO UPDATE SET building=excluded.building, floor=excluded.floor,
                   name=excluded.name, note=excluded.note""",
            (room["id"], room.get("building", ""), room.get("floor", ""), room["name"], room.get("note", ""),
             time.time()))
        _conn.commit()


def save_room_building(room):
    """ห้องที่ใส่ชื่ออาคารใหม่ -> สร้างโฟลเดอร์อาคารให้อัตโนมัติ"""
    if room.get("building"):
        add_building(room["building"])


def list_buildings():
    if _conn is None:
        return []
    with _lock:
        rows = _conn.execute("SELECT name, sort_order FROM buildings").fetchall()
    return [dict(r) for r in rows]


def add_building(name):
    with _lock:
        cur = _conn.execute("INSERT OR IGNORE INTO buildings (name, created_at) VALUES (?, ?)", (name, time.time()))
        _conn.commit()
    return cur.rowcount == 1


def delete_building(name):
    """ลบโฟลเดอร์อาคาร (ต้องไม่มีห้องเหลืออยู่)"""
    with _lock:
        if _conn.execute("SELECT 1 FROM rooms WHERE building=? LIMIT 1", (name,)).fetchone():
            return False
        _conn.execute("DELETE FROM buildings WHERE name=?", (name,))
        _conn.commit()
    return True


def set_order(table, keys):
    """จัดลำดับใหม่ตามรายการที่ส่งมา (ตัวแรก = 1)"""
    col = {"buildings": "name", "rooms": "id", "cameras": "id", "projectors": "id"}[table]
    if _conn is None:
        return
    with _lock:
        for i, k in enumerate(keys, 1):
            _conn.execute(f"UPDATE {table} SET sort_order=? WHERE {col}=?", (i, k))
        _conn.commit()


def set_order_one(table, key, value):
    if _conn is None or table not in ("cameras", "projectors"):
        return
    with _lock:
        _conn.execute(f"UPDATE {table} SET sort_order=? WHERE id=?", (value, key))
        _conn.commit()


def rename_building(old, new):
    """เปลี่ยนชื่ออาคาร = เปลี่ยนให้ทุกห้องที่อยู่อาคารนั้น คืนจำนวนห้องที่เปลี่ยน"""
    with _lock:
        if new != old and _conn.execute("SELECT 1 FROM buildings WHERE name=?", (new,)).fetchone():
            _conn.execute("DELETE FROM buildings WHERE name=?", (old,))  # รวมกับอาคารชื่อนี้ที่มีอยู่แล้ว
            b = 1
        else:
            b = _conn.execute("UPDATE buildings SET name=? WHERE name=?", (new, old)).rowcount
        n = _conn.execute("UPDATE rooms SET building=? WHERE building=?", (new, old)).rowcount
        _conn.commit()
    return n or b  # จำนวนห้องที่ย้าย (อาคารเปล่า = 1, ไม่พบ = 0)


def delete_room(room_id):
    """ลบห้อง: กล้อง/โปรเจกเตอร์ในห้องไม่ถูกลบ แค่ย้ายไปอยู่ในกลุ่ม ยังไม่ระบุห้อง"""
    with _lock:
        _conn.execute("UPDATE cameras SET room_id=NULL WHERE room_id=?", (room_id,))
        _conn.execute("UPDATE projectors SET room_id=NULL WHERE room_id=?", (room_id,))
        _conn.execute("DELETE FROM rooms WHERE id=?", (room_id,))
        _conn.commit()


# ---------------------- โปรเจกเตอร์ ----------------------
def load_projectors():
    if _conn is None:
        return []
    with _lock:
        rows = _conn.execute("SELECT * FROM projectors ORDER BY created_at").fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["password"] = decrypt(d.pop("password_enc"))
        out.append(d)
    return out


def save_projector(p):
    if _conn is None:
        return
    now = time.time()
    info = p.info or {}
    with _lock:
        _conn.execute(
            """INSERT INTO projectors (id, room_id, name, ip, pjlink_port, control_port, username, password_enc,
                   model, serial, projector_name, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(id) DO UPDATE SET
                   room_id=excluded.room_id, name=excluded.name, ip=excluded.ip,
                   pjlink_port=excluded.pjlink_port, control_port=excluded.control_port,
                   username=excluded.username, password_enc=excluded.password_enc,
                   model=CASE WHEN excluded.model != '' THEN excluded.model ELSE projectors.model END,
                   serial=CASE WHEN excluded.serial != '' THEN excluded.serial ELSE projectors.serial END,
                   projector_name=CASE WHEN excluded.projector_name != '' THEN excluded.projector_name
                                       ELSE projectors.projector_name END,
                   updated_at=excluded.updated_at""",
            (p.id, p.room_id, p.name, p.ip, p.pjlink_port, p.control_port, p.user, encrypt(p.password),
             info.get("model", ""), info.get("serial", ""), info.get("projector_name", ""), now, now))
        _conn.commit()


def update_projector_runtime(proj_id, **fields):
    allowed = {"last_status", "last_error", "lamp_hours", "last_online_at", "model", "serial", "projector_name"}
    fields = {k: v for k, v in fields.items() if k in allowed}
    if not fields or _conn is None:
        return
    cols = ", ".join(f"{k}=?" for k in fields)
    with _lock:
        _conn.execute(f"UPDATE projectors SET {cols} WHERE id=?", (*fields.values(), proj_id))
        _conn.commit()


def set_device_room(table, dev_id, room_id):
    """ย้ายกล้อง/โปรเจกเตอร์เข้าห้อง (room_id=None = ยังไม่ระบุห้อง)"""
    assert table in ("cameras", "projectors")
    if _conn is None:
        return
    with _lock:
        _conn.execute(f"UPDATE {table} SET room_id=? WHERE id=?", (room_id, dev_id))
        _conn.commit()


def delete_projector(proj_id):
    if _conn is None:
        return
    with _lock:
        _conn.execute("DELETE FROM projectors WHERE id=?", (proj_id,))
        _conn.commit()
