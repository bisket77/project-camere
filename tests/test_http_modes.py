"""ทดสอบโหมดดึงภาพผ่าน HTTP กับกล้อง Dahua จำลอง (กรณี RTSP ใช้ไม่ได้)

- MJPEG ผ่าน HTTP → ต้องได้สถานะ mjpeg และมี fps
- กล้องเปิดการเชื่อมต่อค้างแต่ไม่ส่งภาพ → ต้องสลับไปโหมดภาพนิ่งเอง (เคยค้าง 5 นาที)
- ภาพนิ่งต่อเนื่อง → ต้องได้สถานะ snapshot และมี fps
"""
import threading
import time

import cv2
import numpy as np
import pytest
from flask import Flask, Response
from werkzeug.serving import make_server

import app as camapp
from conftest import closed_port

BOUNDARY = b"--myboundary" + bytes([13, 10])
MODE = {"mjpeg": "ok"}  # ok / silent / none


def _jpg(i):
    img = np.zeros((180, 320, 3), np.uint8)
    cv2.putText(img, str(i), (20, 120), cv2.FONT_HERSHEY_SIMPLEX, 3, (255, 255, 255), 4)
    return cv2.imencode(".jpg", img)[1].tobytes()


fake = Flask("fake_dahua")


@fake.get("/cgi-bin/snapshot.cgi")
def snap():
    time.sleep(0.02)
    return Response(_jpg(0), mimetype="image/jpeg")


@fake.get("/cgi-bin/magicBox.cgi")
def magic():
    return "deviceType=FAKE-IPC\nserialNumber=SN-TEST\nversion=1.0\nname=fake\n"


@fake.get("/cgi-bin/mjpg/video.cgi")
def mjpg():
    if MODE["mjpeg"] == "none":
        return Response("Error", status=400)

    def gen():
        i = 0
        while True:
            if MODE["mjpeg"] == "silent":  # ส่งแค่ boundary ไม่มีภาพ
                yield BOUNDARY
                time.sleep(0.5)
                continue
            d = _jpg(i)
            i += 1
            yield BOUNDARY + b"Content-Type: image/jpeg" + bytes([13, 10, 13, 10]) + d + bytes([13, 10])
            time.sleep(1 / 20)
    return Response(gen(), mimetype="multipart/x-mixed-replace; boundary=myboundary")


@pytest.fixture(scope="module")
def fake_camera():
    srv = make_server("127.0.0.1", 0, fake, threaded=True)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv.server_port
    srv.shutdown()


def _run(http_port, mode, wait_status, timeout=20):
    MODE["mjpeg"] = mode
    cam = camapp.Camera("t", "127.0.0.1", "admin", "x", "dahua", "sub", "", http_port, closed_port())
    cam.viewers = 1  # จำลองมีคนเปิดดูภาพสด
    try:
        end = time.time() + timeout
        while time.time() < end:
            if cam.status == wait_status and cam.fps > 0 and cam.jpeg:
                return cam.to_dict()
            time.sleep(0.3)
        pytest.fail(f"ไม่ถึงสถานะ {wait_status} (ตอนนี้ {cam.status}, fps={cam.fps})")
    finally:
        cam.stop()


def test_mjpeg_over_http(fake_camera):
    d = _run(fake_camera, "ok", "mjpeg")
    assert d["mode"] == "mjpeg" and d["resolution"] == "320x180"


def test_silent_mjpeg_falls_back_to_snapshot(fake_camera):
    d = _run(fake_camera, "silent", "snapshot")
    assert d["mode"] == "snapshot"


def test_snapshot_mode(fake_camera):
    d = _run(fake_camera, "none", "snapshot")
    assert d["fps"] > 1


def test_device_info_fetched(fake_camera):
    MODE["mjpeg"] = "none"
    cam = camapp.Camera("t", "127.0.0.1", "admin", "x", "dahua", "sub", "", fake_camera, closed_port())
    try:
        end = time.time() + 10
        while time.time() < end and not cam.info:
            time.sleep(0.3)
        assert cam.info.get("model") == "FAKE-IPC"
        assert cam.info.get("serial") == "SN-TEST"
    finally:
        cam.stop()
