"""ทดสอบการส่ง H.264 ให้เบราว์เซอร์ตรง ๆ (fragmented MP4) ด้วยวิดีโอทดสอบแทนกล้อง RTSP"""
import threading
import time

import numpy as np
import pytest

import app as camapp

av = pytest.importorskip("av")


@pytest.fixture(scope="module")
def h264_file(tmp_path_factory):
    path = str(tmp_path_factory.mktemp("vid") / "test.mp4")
    out = av.open(path, "w")
    stream = None
    for enc in ("libx264", "h264", "libopenh264"):
        try:
            stream = out.add_stream(enc, rate=25)
            break
        except Exception:
            continue
    if stream is None:
        pytest.skip("ไม่มีตัวเข้ารหัส H.264 ใน PyAV ชุดนี้")
    stream.width, stream.height, stream.pix_fmt = 320, 180, "yuv420p"
    for i in range(50):
        img = np.zeros((180, 320, 3), np.uint8)
        img[:, (i * 6) % 320:(i * 6) % 320 + 20] = (0, 200, 255)
        for p in stream.encode(av.VideoFrame.from_ndarray(img, format="rgb24")):
            out.mux(p)
    for p in stream.encode():
        out.mux(p)
    out.close()
    return path


def _fake_rtsp_camera(video_path):
    cam = camapp.Camera.__new__(camapp.Camera)
    cam.__dict__.update(dict(
        id="h264test", name="h264", ip="127.0.0.1", user="", password="", brand="other", quality="sub",
        custom_rtsp=video_path, rtsp_input="", rtsp_tls=False, _tls_proxy=None, http_port=80, rtsp_port=554,
        status="online", last_error="", mode="rtsp", info={}, latency_ms=1.0, resolution="", fps=0.0,
        online_since=time.time(), added_at=0, viewers=0, jpeg=None, jpeg_time=0.0,
        frame_cond=threading.Condition(), _last_req_time=0.0, running=True,
        wake=threading.Event(), monitor_wake=threading.Event()))
    # rtsp_url() ของไฟล์ทดสอบ: คืน path ตรง ๆ
    cam.rtsp_url = lambda hide_password=False: video_path
    return cam


def test_live_mp4_streams_fragmented_mp4(client, h264_file):
    cam = _fake_rtsp_camera(h264_file)
    camapp.cameras[cam.id] = cam
    r = client.get(f"/api/cameras/{cam.id}/live.mp4", buffered=False)
    assert r.status_code == 200
    assert r.headers["X-Codec"].startswith("avc1.")
    data = b""
    for chunk in r.response:
        data += chunk
        if len(data) > 4000:
            break
    r.close()
    assert b"ftyp" in data[:64]   # หัวไฟล์ MP4
    assert b"moof" in data        # มี fragment วิดีโอแล้ว (ไม่ต้องรอทั้งไฟล์)


def test_live_mp4_requires_rtsp_mode(client):
    cam = _fake_rtsp_camera("unused")
    cam.mode = "snapshot"
    camapp.cameras[cam.id] = cam
    assert client.get(f"/api/cameras/{cam.id}/live.mp4").status_code == 409
