"""ทดสอบ web/ptz.py ด้วยคำตอบกล้องจำลอง (ไม่ต่อกล้องจริง)"""
import os
import sys
import types

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "web"))
import ptz  # noqa: E402


def cam(brand="dahua", cid="c1"):
    return types.SimpleNamespace(id=cid, brand=brand, ip="10.0.0.9", http_port=80, user="u", password="p")


class Resp:
    def __init__(self, text, code=200):
        self.text, self.status_code = text, code


def fake_get(routes, log):
    def get(url, auth=None, timeout=None):
        log.append(url)
        for key, resp in routes.items():
            if key in url:
                return resp
        return Resp("Error", 400)
    return get


DAHUA_CAPS = "caps.Pan=true\ncaps.Tile=true\ncaps.Zoom=true\ncaps.Focus=false\ncaps.Preset=true\n"
DAHUA_PRESETS = "presets[0].Index=1\npresets[0].Name=หน้าห้อง\npresets[1].Index=3\npresets[1].Name=กระดาน\n"


def test_dahua_caps_and_presets(monkeypatch):
    ptz._cache.clear()
    log = []
    monkeypatch.setattr(ptz.requests, "get", fake_get({
        "getCurrentProtocolCaps": Resp(DAHUA_CAPS), "getPresets": Resp(DAHUA_PRESETS)}, log))
    c = ptz.caps(cam())
    assert c["supported"] and c["pan_tilt"] and c["zoom"] and not c["focus"]
    assert c["presets"] == [{"index": 1, "name": "หน้าห้อง"}, {"index": 3, "name": "กระดาน"}]
    n = len(log)
    ptz.caps(cam())                         # ครั้งที่ 2 ใช้ผลที่จำไว้ ไม่ถามกล้องซ้ำ
    assert len(log) == n


def test_dahua_without_ptz_is_not_supported(monkeypatch):
    ptz._cache.clear()
    monkeypatch.setattr(ptz.requests, "get", fake_get({}, []))   # กล้องตอบ 400 = ไม่มี PTZ
    assert ptz.caps(cam(cid="fixed")) == {"supported": False}


def test_other_brand_not_supported():
    assert ptz.caps(cam(brand="other", cid="x")) == {"supported": False}


def test_dahua_start_stop_preset_urls(monkeypatch):
    ptz._cache.clear()
    log = []
    monkeypatch.setattr(ptz.requests, "get", fake_get({
        "getCurrentProtocolCaps": Resp(DAHUA_CAPS), "getPresets": Resp(""), "action=start": Resp("OK"),
        "action=stop": Resp("OK")}, log))
    c = cam(cid="m")
    ptz.start(c, "upleft", 9)               # ความเร็วเกิน 8 -> ตัดเหลือ 8
    assert any("action=start" in u and "code=LeftUp" in u and "arg2=8" in u for u in log)
    ptz.stop(c, "upleft")
    assert any("action=stop" in u and "code=LeftUp" in u for u in log)
    assert "m" not in ptz._timers           # stop ยกเลิกตัวหยุดอัตโนมัติแล้ว
    ptz.goto_preset(c, 3)
    assert any("code=GotoPreset" in u and "arg2=3" in u for u in log)


def test_unknown_move_rejected():
    try:
        ptz.start(cam(brand="other"), "spin", 4)
    except ptz.PTZError:
        return
    raise AssertionError("ต้อง error")
