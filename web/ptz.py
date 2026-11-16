"""ควบคุมหมุน / ซูม / โฟกัส / ตำแหน่งจำ (PTZ) ของกล้อง — แสดงปุ่มเฉพาะกล้องที่รองรับจริง

ยี่ห้อที่รองรับ
  dahua     : /cgi-bin/ptz.cgi (ถามความสามารถด้วย getCurrentProtocolCaps)
  hikvision : /ISAPI/PTZCtrl/channels/1 (ถามความสามารถด้วย capabilities)
  อื่น ๆ    : ยังไม่รองรับ (คืน supported=False) — เพิ่มยี่ห้อใหม่ได้ใน DRIVERS

ใช้กับ app.py:  caps(cam) -> dict,  start(cam, move, speed),  stop(cam, move),  goto_preset(cam, index)
move = up/down/left/right/upleft/upright/downleft/downright/zoom_in/zoom_out/focus_near/focus_far
speed = 1..8
"""
import re
import threading
import time

import requests
from requests.auth import HTTPDigestAuth

MOVES = ("up", "down", "left", "right", "upleft", "upright", "downleft", "downright",
         "zoom_in", "zoom_out", "focus_near", "focus_far")
AUTO_STOP_SEC = 8      # กันกล้องหมุนค้างถ้าคำสั่ง stop หาย (ปิดเบราว์เซอร์ระหว่างกดค้าง ฯลฯ)
CAPS_TTL_SEC = 600     # จำผลถามความสามารถ 10 นาที


class PTZError(Exception):
    pass


def _get(cam, path, timeout=4):
    url = f"http://{cam.ip}:{cam.http_port}{path}"
    try:
        r = requests.get(url, auth=HTTPDigestAuth(cam.user, cam.password), timeout=timeout)
    except requests.RequestException as e:
        raise PTZError(f"ติดต่อกล้องไม่ได้ ({e.__class__.__name__})")
    if r.status_code == 401:
        raise PTZError("username หรือ password ไม่ถูกต้อง")
    return r


def _put(cam, path, xml, timeout=4):
    url = f"http://{cam.ip}:{cam.http_port}{path}"
    try:
        r = requests.put(url, data=xml.encode(), headers={"Content-Type": "application/xml"},
                         auth=HTTPDigestAuth(cam.user, cam.password), timeout=timeout)
    except requests.RequestException as e:
        raise PTZError(f"ติดต่อกล้องไม่ได้ ({e.__class__.__name__})")
    if r.status_code == 401:
        raise PTZError("username หรือ password ไม่ถูกต้อง")
    if r.status_code >= 400:
        raise PTZError(f"กล้องไม่รับคำสั่ง ({r.status_code})")
    return r


def _kv(text):
    out = {}
    for line in text.splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip()
    return out


# ---------------- Dahua ----------------
DAHUA_CODE = {"up": "Up", "down": "Down", "left": "Left", "right": "Right", "upleft": "LeftUp",
              "upright": "RightUp", "downleft": "LeftDown", "downright": "RightDown",
              "zoom_in": "ZoomTele", "zoom_out": "ZoomWide", "focus_near": "FocusNear", "focus_far": "FocusFar"}


def _dahua_caps(cam):
    r = _get(cam, "/cgi-bin/ptz.cgi?action=getCurrentProtocolCaps&channel=1")
    if r.status_code != 200 or "caps." not in r.text:
        return None
    c = _kv(r.text)
    yes = lambda *keys: any(c.get(f"caps.{k}", "").lower() == "true" for k in keys)  # noqa: E731
    caps = {"pan_tilt": yes("Pan", "Tile", "Tilt"), "zoom": yes("Zoom"), "focus": yes("Focus"),
            "presets": []}
    if yes("Preset"):
        try:
            p = _kv(_get(cam, "/cgi-bin/ptz.cgi?action=getPresets&channel=1").text)
            for k, v in p.items():
                m = re.fullmatch(r"presets\[(\d+)\]\.Index", k)
                if m:
                    caps["presets"].append({"index": int(v), "name": p.get(f"presets[{m.group(1)}].Name", f"#{v}")})
        except PTZError:
            pass
        caps["presets"].sort(key=lambda x: x["index"])
    return caps if (caps["pan_tilt"] or caps["zoom"]) else None


def _dahua_cmd(cam, action, code, arg2):
    r = _get(cam, f"/cgi-bin/ptz.cgi?action={action}&channel=1&code={code}&arg1=0&arg2={arg2}&arg3=0")
    if r.status_code != 200 or "OK" not in r.text:
        raise PTZError(f"กล้องไม่รับคำสั่ง ({r.status_code} {r.text.strip()[:60]})")


def _dahua_start(cam, move, speed):
    _dahua_cmd(cam, "start", DAHUA_CODE[move], speed)


def _dahua_stop(cam, move):
    _dahua_cmd(cam, "stop", DAHUA_CODE[move], 0)


def _dahua_preset(cam, index):
    _dahua_cmd(cam, "start", "GotoPreset", index)


# ---------------- Hikvision ----------------
HIK_VEC = {"up": (0, 1, 0), "down": (0, -1, 0), "left": (-1, 0, 0), "right": (1, 0, 0),
           "upleft": (-1, 1, 0), "upright": (1, 1, 0), "downleft": (-1, -1, 0), "downright": (1, -1, 0),
           "zoom_in": (0, 0, 1), "zoom_out": (0, 0, -1)}


def _hik_caps(cam):
    r = _get(cam, "/ISAPI/PTZCtrl/channels/1/capabilities")
    if r.status_code != 200 or "PTZChanelCap" not in r.text and "PTZChannelCap" not in r.text:
        return None
    t = r.text
    caps = {"pan_tilt": "ContinuousPanTiltSpace" in t, "zoom": "ContinuousZoomSpace" in t,
            "focus": False, "presets": []}
    try:
        p = _get(cam, "/ISAPI/PTZCtrl/channels/1/presets").text
        for m in re.finditer(r"<PTZPreset>.*?<id>(\d+)</id>.*?<presetName>(.*?)</presetName>", p, re.S):
            caps["presets"].append({"index": int(m.group(1)), "name": m.group(2) or f"#{m.group(1)}"})
    except PTZError:
        pass
    return caps if (caps["pan_tilt"] or caps["zoom"]) else None


def _hik_continuous(cam, pan, tilt, zoom):
    _put(cam, "/ISAPI/PTZCtrl/channels/1/continuous",
         f"<PTZData><pan>{pan}</pan><tilt>{tilt}</tilt><zoom>{zoom}</zoom></PTZData>")


def _hik_start(cam, move, speed):
    if move not in HIK_VEC:
        raise PTZError("กล้องรุ่นนี้ไม่รองรับคำสั่งนี้")
    s = int(speed) * 12  # 1..8 -> 12..96
    pan, tilt, zoom = HIK_VEC[move]
    _hik_continuous(cam, pan * s, tilt * s, zoom * s)


def _hik_stop(cam, move):
    _hik_continuous(cam, 0, 0, 0)


def _hik_preset(cam, index):
    _put(cam, f"/ISAPI/PTZCtrl/channels/1/presets/{int(index)}/goto", "")


DRIVERS = {
    "dahua": (_dahua_caps, _dahua_start, _dahua_stop, _dahua_preset),
    "hikvision": (_hik_caps, _hik_start, _hik_stop, _hik_preset),
}

_cache = {}           # cam.id -> (เวลา, caps หรือ None)
_timers = {}          # cam.id -> threading.Timer (หยุดอัตโนมัติ)
_lock = threading.Lock()


def caps(cam, refresh=False):
    """{"supported": bool, "pan_tilt", "zoom", "focus", "presets": [{index, name}], "error"?}"""
    drv = DRIVERS.get(cam.brand)
    if not drv:
        return {"supported": False}
    with _lock:
        hit = _cache.get(cam.id)
    if hit and not refresh and time.time() - hit[0] < CAPS_TTL_SEC:
        c = hit[1]
    else:
        try:
            c = drv[0](cam)
        except PTZError as e:
            return {"supported": False, "error": str(e)}
        with _lock:
            _cache[cam.id] = (time.time(), c)
    return {"supported": True, **c} if c else {"supported": False}


def _driver(cam):
    drv = DRIVERS.get(cam.brand)
    if not drv or not caps(cam).get("supported"):
        raise PTZError("กล้องตัวนี้ไม่รองรับการหมุน / ซูม")
    return drv


def start(cam, move, speed=4):
    if move not in MOVES:
        raise PTZError("ไม่รู้จักคำสั่งนี้")
    speed = max(1, min(8, int(speed or 4)))
    drv = _driver(cam)
    drv[1](cam, move, speed)
    with _lock:  # หยุดเองถ้าไม่มีคำสั่ง stop ตามมา
        old = _timers.pop(cam.id, None)
        if old:
            old.cancel()
        t = threading.Timer(AUTO_STOP_SEC, _safe_stop, (cam, move))
        t.daemon = True
        _timers[cam.id] = t
        t.start()


def _safe_stop(cam, move):
    try:
        stop(cam, move)
    except PTZError:
        pass


def stop(cam, move):
    if move not in MOVES:
        raise PTZError("ไม่รู้จักคำสั่งนี้")
    with _lock:
        t = _timers.pop(cam.id, None)
        if t:
            t.cancel()
    _driver(cam)[2](cam, move)


def goto_preset(cam, index):
    _driver(cam)[3](cam, int(index))


def forget(cam_id):
    """แก้ไข/ลบกล้อง -> ล้างผลถามความสามารถเดิม"""
    with _lock:
        _cache.pop(cam_id, None)
