"""ทดสอบฐานข้อมูล: บันทึก/โหลด/ลบ และรหัสผ่านต้องถูกเข้ารหัส"""
import os
import sqlite3
from types import SimpleNamespace

import db


def _cam(cam_id="dbtest01", password="s3cret-pass", rtsp=""):
    return SimpleNamespace(id=cam_id, name="ห้อง B1118", brand="dahua", ip="192.0.2.10", http_port=80,
                           rtsp_port=554, quality="sub", user="admin", password=password,
                           custom_rtsp=rtsp, info={"model": "IPC-TEST", "serial": "SN1"})


def test_save_and_load_roundtrip():
    db.save_camera(_cam())
    try:
        row = next(r for r in db.load_all() if r["id"] == "dbtest01")
        assert row["password"] == "s3cret-pass"
        assert row["ip"] == "192.0.2.10"
        assert row["model"] == "IPC-TEST"
    finally:
        db.delete_camera("dbtest01")
    assert all(r["id"] != "dbtest01" for r in db.load_all())


def test_password_not_stored_in_plaintext():
    db.save_camera(_cam("dbtest02", password="very-unique-pw-123", rtsp="rtsp://u:very-unique-pw-123@h/x"))
    try:
        raw = b""
        for path in (db.DB_PATH, db.DB_PATH + "-wal"):
            if os.path.exists(path):
                with open(path, "rb") as f:
                    raw += f.read()
        assert b"very-unique-pw-123" not in raw
        enc = sqlite3.connect(db.DB_PATH).execute(
            "SELECT password_enc FROM cameras WHERE id='dbtest02'").fetchone()[0]
        assert enc and enc != "very-unique-pw-123"
    finally:
        db.delete_camera("dbtest02")


def test_update_runtime_and_keep_device_info():
    db.save_camera(_cam("dbtest03"))
    try:
        db.update_runtime("dbtest03", last_status="online", last_mode="rtsp", rtsp_tls=1)
        cam = _cam("dbtest03")
        cam.info = {}  # แก้ไขกล้องโดยไม่มีข้อมูลเครื่อง → ต้องไม่ลบรุ่นเดิมทิ้ง
        db.save_camera(cam)
        row = next(r for r in db.load_all() if r["id"] == "dbtest03")
        assert (row["last_status"], row["last_mode"], row["rtsp_tls"]) == ("online", "rtsp", 1)
        assert row["model"] == "IPC-TEST"
    finally:
        db.delete_camera("dbtest03")


def test_old_database_gets_room_column(tmp_path, monkeypatch):
    """ฐานข้อมูลรุ่นก่อนมีห้อง: เปิดแล้วต้องเพิ่มคอลัมน์ room_id ให้ และกล้องเดิมยังอยู่"""
    path = str(tmp_path / "old.db")
    old = sqlite3.connect(path)
    old.executescript(db.SCHEMA.split("CREATE TABLE IF NOT EXISTS rooms")[0])
    old.execute("INSERT INTO cameras (id, name, brand, ip, created_at, updated_at) "
                "VALUES ('old1', 'กล้องเก่า', 'dahua', '192.0.2.50', 0, 0)")
    old.commit()
    old.close()
    saved = db._conn
    monkeypatch.setattr(db, "DB_PATH", path)
    try:
        db.init()
        rows = db.load_all()
        assert rows[0]["id"] == "old1" and rows[0]["room_id"] is None
        assert db.list_rooms() == [] and db.load_projectors() == []
    finally:
        db._conn.close()
        db._conn = saved


def test_wrong_key_gives_empty_password():
    assert db.decrypt("not-a-valid-token") == ""
