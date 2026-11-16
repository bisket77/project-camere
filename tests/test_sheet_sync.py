"""ทดสอบการสั่งซิงก์ Google Sheet ทันที (ไฟล์ web/data/.sync_now)"""
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "web"))
import sheet_sync  # noqa: E402


def make(tmp_path, poll_sec=30):
    s = sheet_sync.SheetSync(None, "http://x", "t", poll_sec=poll_sec, flag_path=str(tmp_path / ".sync_now"))
    s.version = "123"
    s.wait_until["10.0.0.1"] = time.time() + 600
    return s


def test_no_flag_does_nothing(tmp_path):
    s = make(tmp_path)
    assert s.take_sync_flag() is False
    assert s.version == "123"
    assert "10.0.0.1" in s.wait_until


def test_flag_resets_version_and_waits(tmp_path):
    s = make(tmp_path)
    (tmp_path / ".sync_now").touch()
    assert s.take_sync_flag() is True
    assert s.version == ""
    assert s.wait_until == {}
    assert not (tmp_path / ".sync_now").exists()


def test_camera_plan_by_model():
    plan = sheet_sync.camera_plan
    assert plan({"model": "DH-SF125-S2", "ip": "10.0.0.5"}) == ("dahua", "")
    assert plan({"model": "dahua", "ip": "10.0.0.5"}) == ("dahua", "")
    assert plan({"model": "WV-SP105", "ip": "10.0.0.5", "port": "554"}) == \
        ("other", "rtsp://10.0.0.5:554/MediaInput/h264")
    assert plan({"model": "IP570P", "ip": "10.0.0.6", "port": ""}) == \
        ("other", "rtsp://10.0.0.6:554/medias1")
    assert plan({"model": "UNIFI", "ip": "10.0.0.7"}) == ("other", "")
    assert plan({"model": "UNIFI", "ip": "10.0.0.7", "rtsp": "rtsps://10.0.0.1:7441/abc"}) == \
        ("other", "rtsps://10.0.0.1:7441/abc")
    assert plan({"model": "XYZ-1", "ip": "10.0.0.8"}) == ("dahua", "")


def test_strip_auth():
    assert sheet_sync._strip_auth("rtsps://u:p@10.0.0.1:7441/abc") == "rtsps://10.0.0.1:7441/abc"
    assert sheet_sync._strip_auth("") == ""


def test_wait_next_returns_early_on_flag(tmp_path):
    s = make(tmp_path, poll_sec=30)
    (tmp_path / ".sync_now").touch()
    t = time.time()
    s.wait_next()
    assert time.time() - t < 2
    assert s.version == ""



def test_lookup_returns_sheet_values_for_device(tmp_path):
    s = make(tmp_path)
    s.rows = [
        {"type": "CCTV", "ip": "10.0.0.5", "model": "IP570P", "user": "Dmonitor", "password": "pw1", "room": "F9113"},
        {"type": "Projector", "ip": "10.0.0.5", "model": "VX420", "user": "admin1", "password": "pw2"},
    ]
    cam = s.lookup("cameras", {"10.0.0.5"})
    assert cam["model"] == "IP570P" and cam["user"] == "Dmonitor" and cam["password"] == "pw1"
    assert s.lookup("projectors", {"10.0.0.5"})["model"] == "VX420"
    assert s.lookup("cameras", {"10.0.0.9"}) is None
