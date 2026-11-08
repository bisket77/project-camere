"""ตั้งค่ากลางของการทดสอบ: ใช้ฐานข้อมูลและกุญแจชั่วคราว ไม่แตะข้อมูลจริงใน web/data/"""
import os
import sys
import tempfile

import pytest
from cryptography.fernet import Fernet

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "web"))

_tmp = tempfile.mkdtemp(prefix="camtest-")
os.environ["CAMERA_DB_PATH"] = os.path.join(_tmp, "test.db")
os.environ["CAMERA_SECRET_KEY"] = Fernet.generate_key().decode()

import app as camapp  # noqa: E402
import db  # noqa: E402

db.init()


@pytest.fixture
def client():
    camapp.app.config["TESTING"] = True
    yield camapp.app.test_client()
    # ปิดกล้องทุกตัวที่เทสต์สร้างไว้
    with camapp.cameras_lock:
        cams = list(camapp.cameras.values())
        camapp.cameras.clear()
    for c in cams:
        c.stop()
        db.delete_camera(c.id)


def closed_port():
    """หาพอร์ตในเครื่องที่ไม่มีใครฟังอยู่ (ใช้จำลองกล้องที่ติดต่อไม่ได้)"""
    import socket
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port
