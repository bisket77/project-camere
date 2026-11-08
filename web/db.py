"""ฐานข้อมูลกล้อง (SQLite ไฟล์เดียว: web/data/cameras.db)

เก็บ: กล้องที่เพิ่มไว้ (IP, ยี่ห้อ, พอร์ต, username, password) + สถานะล่าสุด + ข้อมูลเครื่อง
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
                   password_enc, rtsp_url_enc, model, serial, firmware, device_name, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(id) DO UPDATE SET
                   name=excluded.name, brand=excluded.brand, ip=excluded.ip,
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
             info.get("firmware", ""), info.get("device_name", ""), now, now))
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
