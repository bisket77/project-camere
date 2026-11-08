"""ควบคุม/ดูสถานะโปรเจกเตอร์ผ่าน LAN

ใช้ 2 โปรโตคอล (เหมือนที่โปรแกรม Multi Projector Monitoring ของ Panasonic ใช้)
1) PJLink (พอร์ต 4352) มาตรฐานกลาง ทุกยี่ห้อรองรับ:
   เปิด/ปิดเครื่อง, เลือก input, AV mute, ชั่วโมงหลอดไฟ, ผลตรวจตัวเอง (พัดลม/หลอด/อุณหภูมิ/ฟิลเตอร์), รุ่น, ชื่อ
2) Panasonic NTCONTROL (พอร์ต 1024) ใช้ username + password ของเครื่อง:
   ใช้เสริมสิ่งที่ PJLink ไม่มี เช่น อุณหภูมิ, picture mode, volume (ถ้าเครื่องไม่ตอบ ก็แค่ไม่แสดงค่านั้น)

ไม่มีการเก็บภาพ/บันทึกอะไรนอกจากสถานะล่าสุดใน DB
"""
import hashlib
import re
import socket
import threading
import time
import uuid

POLL_SEC = 5          # ถามสถานะหลัก (PJLink) ทุก 5 วินาที
EXTRA_SEC = 30        # อุณหภูมิ / picture mode (NTCONTROL ต้องต่อใหม่ทุกคำสั่ง ช้า) ถามทุก 30 วินาที
TIMEOUT = 3

STATUS_TEXT = {
    "connecting": "กำลังเชื่อมต่อ", "on": "เปิดอยู่", "standby": "Standby",
    "warming": "กำลังวอร์มเครื่อง", "cooling": "กำลังระบายความร้อน",
    "offline": "ติดต่อไม่ได้", "auth_error": "รหัสผ่านผิด", "error": "เครื่องแจ้งเตือน",
}
POWER_STATE = {"0": "standby", "1": "on", "2": "cooling", "3": "warming"}

# input: ชื่อบนหน้าเว็บ → (รหัส PJLink, รหัส Panasonic)
INPUTS = {
    "COMPUTER1": ("11", "RG1"), "COMPUTER2": ("12", "RG2"),
    "VIDEO": ("21", "VID"), "S-VIDEO": ("22", "SVD"), "HDMI": ("31", "HD1"),
}
PJLINK_INPUT_NAME = {v[0]: k for k, v in INPUTS.items()}
# picture mode ของ Panasonic (คำสั่ง VPM) — ถ้ารุ่นไหนรหัสต่างไป เครื่องจะตอบ ERR แล้วหน้าเว็บแจ้งให้
PICTURE_MODES = {"STANDARD": "STD", "DYNAMIC": "DYN", "NATURAL": "NAT",
                 "BLACKBOARD": "BLB", "WHITEBOARD": "WHB", "CINEMA": "CIN"}
PICTURE_NAME = {v: k for k, v in PICTURE_MODES.items()}
ERST_PARTS = ("พัดลม", "หลอดไฟ", "อุณหภูมิ", "ฝาครอบ", "ฟิลเตอร์", "อื่น ๆ")
ERR_TEXT = {"ERR1": "คำสั่งไม่รองรับ", "ERR2": "ค่าไม่ถูกต้อง",
            "ERR3": "ทำไม่ได้ในสถานะนี้ (เช่น เครื่องปิดอยู่)", "ERR4": "เครื่องขัดข้อง"}


class ProjectorAuthError(Exception):
    pass


class ProjectorError(Exception):
    pass


def _readline(sock):
    buf = b""
    while not buf.endswith(b"\r"):
        ch = sock.recv(1)
        if not ch:
            break
        buf += ch
    return buf.decode("ascii", "replace").strip()


# ---------------------- PJLink ----------------------
def pjlink_query(ip, port, password, commands, timeout=TIMEOUT):
    """ส่งหลายคำสั่งใน 1 การเชื่อมต่อ เช่น ["%1POWR ?", "%1LAMP ?"] → {"%1POWR": "1", ...}

    ค่าที่ได้เป็นข้อความหลังเครื่องหมาย = หรือ "ERR1".."ERR4"
    """
    out = {}
    with socket.create_connection((ip, port), timeout=timeout) as s:
        s.settimeout(timeout)
        hello = _readline(s)
        if hello == "PJLINK ERRA":
            raise ProjectorAuthError("PJLink: รหัสผ่านไม่ถูกต้อง")
        m = re.match(r"PJLINK ([01])(?: (\S+))?", hello)
        if not m:
            raise ProjectorError(f"PJLink ตอบไม่ถูกต้อง: {hello[:40]!r}")
        prefix = ""
        if m.group(1) == "1":
            prefix = hashlib.md5(((m.group(2) or "") + (password or "")).encode()).hexdigest()
        for i, cmd in enumerate(commands):
            s.sendall(((prefix if i == 0 else "") + cmd + "\r").encode())
            resp = _readline(s)
            if resp == "PJLINK ERRA":
                raise ProjectorAuthError("PJLink: รหัสผ่านไม่ถูกต้อง")
            key = cmd.split(" ")[0]
            if resp.upper().startswith(key.upper() + "="):
                out[key] = resp[len(key) + 1:]
            else:
                out[key] = resp
    return out


def pjlink_ok(value):
    return value is not None and not str(value).startswith("ERR")


# ---------------------- Panasonic NTCONTROL ----------------------
def ntcontrol(ip, port, user, password, cmd, timeout=TIMEOUT):
    """ส่ง 1 คำสั่ง (เครื่องตัดการเชื่อมต่อหลังตอบทุกครั้ง) คืนค่าข้อความตอบกลับ (ตัด "00" ข้างหน้าแล้ว)"""
    with socket.create_connection((ip, port), timeout=timeout) as s:
        s.settimeout(timeout)
        hello = _readline(s)
        m = re.match(r"NTCONTROL ([01])(?: (\S+))?", hello)
        if not m:
            raise ProjectorError(f"NTCONTROL ตอบไม่ถูกต้อง: {hello[:40]!r}")
        prefix = ""
        if m.group(1) == "1":
            prefix = hashlib.md5(f"{user}:{password}:{m.group(2) or ''}".encode()).hexdigest()
        s.sendall(f"{prefix}00{cmd}\r".encode())
        resp = _readline(s)
    if resp == "ERRA":
        raise ProjectorAuthError("username หรือ password ไม่ถูกต้อง")
    if resp.startswith("ERR"):
        raise ProjectorError(ERR_TEXT.get(resp, resp))
    data = resp[2:] if resp.startswith("00") else resp
    # Panasonic ตอบรหัสผิดพลาดเป็น ER401 (ทำไม่ได้ตอนนี้ เช่น กำลังวอร์ม/standby) / ER402 (ค่าไม่ถูกต้อง)
    if re.fullmatch(r"ER\d{3}", data):
        raise ProjectorError({"ER401": "ทำไม่ได้ในสถานะนี้ (เช่น เครื่องปิด/กำลังวอร์ม)",
                              "ER402": "ค่าไม่ถูกต้อง"}.get(data, f"เครื่องตอบ {data}"))
    return data


def _parse_temp(v):
    """ค่าอุณหภูมิจาก NTCONTROL เช่น '0028' / '+028.0' → 28.0 (อ่านไม่ได้ = None)"""
    m = re.search(r"[-+]?\d+(?:\.\d+)?", v or "")
    if not m:
        return None
    t = float(m.group())
    return t / 10 if abs(t) > 150 else t  # บางรุ่นส่งเป็นทศนิยม 1 ตำแหน่งไม่มีจุด (เช่น 0280)


def tcp_open(ip, port, timeout=1.5):
    try:
        with socket.create_connection((ip, port), timeout=timeout):
            return True
    except OSError:
        return False


# ---------------------- ตัวโปรเจกเตอร์ ----------------------
class Projector:
    def __init__(self, name, ip, user="admin", password="", pjlink_port=4352, control_port=1024,
                 room_id=None, proj_id=None, on_change=None):
        self.id = proj_id or uuid.uuid4().hex[:8]
        self.name = name or f"โปรเจกเตอร์ {ip}"
        self.ip, self.user, self.password = ip, user, password
        self.pjlink_port, self.control_port = int(pjlink_port), int(control_port)
        self.room_id = room_id or None
        self.on_change = on_change   # เรียกเมื่อสถานะเปลี่ยน (ใช้บันทึก DB)
        self.status, self.last_error = "connecting", ""
        self.power = None            # on / standby / warming / cooling
        self.input = None
        self.av_mute = None
        self.picture_mode = None
        self.lamp_hours = None
        self.errors = {}             # {"พัดลม": "ok"/"warning"/"error", ...}
        self.temps = {}              # {"intake": 28.0, "exhaust": 42.0, "optics": 36.0}
        self.info = {}               # model, serial, projector_name, manufacturer, firmware
        self.latency_ms = None
        self.has_pjlink = self.has_ntcontrol = None
        self.last_online_at = None
        self.last_poll = None
        self._extra_at = 0.0         # เวลาที่ถามข้อมูลเสริมล่าสุด (0 = ถามรอบถัดไปเลย)
        self.running = True
        self._wake = threading.Event()
        self._lock = threading.Lock()   # ห้ามส่งคำสั่งพร้อมกัน (เครื่องรับได้ทีละการเชื่อมต่อ)
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    # ---------- ถามสถานะ ----------
    def _set_status(self, status, error=""):
        changed = (status, error) != (self.status, self.last_error)
        self.status, self.last_error = status, error
        if status not in ("offline", "auth_error", "connecting"):
            self.last_online_at = time.time()
        if changed and self.on_change:
            try:
                self.on_change(self)
            except Exception:
                pass

    def _loop(self):
        while self.running:
            try:
                self.poll()
            except Exception as e:  # ไม่ให้ thread ตาย
                self._set_status("offline", f"{e.__class__.__name__}: {e}")
            self._wake.wait(POLL_SEC)
            self._wake.clear()

    def poll(self):
        t0 = time.time()
        if self.has_pjlink is None or not self.has_pjlink:
            self.has_pjlink = tcp_open(self.ip, self.pjlink_port)
        if self.has_ntcontrol is None:
            self.has_ntcontrol = tcp_open(self.ip, self.control_port)
        if not self.has_pjlink and not self.has_ntcontrol:
            self.latency_ms, self.power = None, None
            self._set_status("offline", f"ติดต่อ {self.ip} ไม่ได้ (พอร์ต {self.pjlink_port} / {self.control_port})")
            return
        with self._lock:
            try:
                power_before = self.power
                if self.has_pjlink:
                    self._poll_pjlink()
                else:
                    self._poll_ntcontrol_basic()
                if self.power != power_before:
                    self._extra_at = 0.0  # เปิด/ปิดเครื่องแล้ว → อัปเดต picture mode / อุณหภูมิทันที
                # เวลาตอบของคำสั่งหลัก (ไม่นับข้อมูลเสริมที่ช้า)
                self.latency_ms = round((time.time() - t0) * 1000)
                if self.has_ntcontrol and time.time() - self._extra_at >= EXTRA_SEC:
                    self._extra_at = time.time()
                    self._poll_ntcontrol_extra()
            except ProjectorAuthError as e:
                self._set_status("auth_error", str(e))
                return
            except OSError as e:
                self.has_pjlink = None
                self._set_status("offline", f"ติดต่อไม่ได้: {e.__class__.__name__}")
                return
        self.last_poll = time.time()
        bad = [k for k, v in self.errors.items() if v == "error"]
        if bad:
            self._set_status("error", "เครื่องแจ้งปัญหา: " + ", ".join(bad))
        else:
            self._set_status(self.power or "standby")

    def _poll_pjlink(self):
        cmds = ["%1POWR ?", "%1INPT ?", "%1AVMT ?", "%1LAMP ?", "%1ERST ?"]
        if not self.info.get("model"):
            cmds += ["%1NAME ?", "%1INF1 ?", "%1INF2 ?", "%1CLSS ?"]
        r = pjlink_query(self.ip, self.pjlink_port, self.password, cmds)
        self.power = POWER_STATE.get(r.get("%1POWR"), self.power)
        inp = r.get("%1INPT")
        self.input = PJLINK_INPUT_NAME.get(inp, inp) if pjlink_ok(inp) else None
        av = r.get("%1AVMT")
        self.av_mute = (av in ("11", "21", "31")) if pjlink_ok(av) else None
        lamp = r.get("%1LAMP")
        if pjlink_ok(lamp) and lamp.split():
            self.lamp_hours = int(lamp.split()[0])
        erst = r.get("%1ERST")
        if pjlink_ok(erst) and len(erst) >= 6:
            self.errors = {ERST_PARTS[i]: ("ok", "warning", "error")[int(c)] if c in "012" else "ok"
                           for i, c in enumerate(erst[:6])}
        for key, field in (("%1NAME", "projector_name"), ("%1INF1", "manufacturer"), ("%1INF2", "model")):
            if pjlink_ok(r.get(key)) and r.get(key):
                self.info[field] = r[key].strip()
        if r.get("%1CLSS") == "2" and not self.info.get("serial"):
            r2 = pjlink_query(self.ip, self.pjlink_port, self.password, ["%2SNUM ?", "%2SVER ?"])
            if pjlink_ok(r2.get("%2SNUM")):
                self.info["serial"] = r2["%2SNUM"].strip()
            if pjlink_ok(r2.get("%2SVER")):
                self.info["firmware"] = r2["%2SVER"].strip()

    def _nt(self, cmd):
        return ntcontrol(self.ip, self.control_port, self.user, self.password, cmd)

    def _poll_ntcontrol_basic(self):
        """ไม่มี PJLink → ถามสถานะหลักผ่าน NTCONTROL แทน"""
        self.power = {"000": "standby", "001": "on"}.get(self._nt("QPW"), self.power)
        try:
            code = self._nt("QIN")
            self.input = next((k for k, v in INPUTS.items() if v[1] == code), code)
        except ProjectorError:
            self.input = None
        try:
            self.av_mute = self._nt("QSH") == "1"
        except ProjectorError:
            self.av_mute = None

    def _poll_ntcontrol_extra(self):
        """ข้อมูลเสริม: picture mode / อุณหภูมิ (ไม่รองรับ = ข้าม)"""
        if self.power == "on":  # ตอนปิด/วอร์มเครื่อง ถาม picture mode ไม่ได้ (ER401)
            try:
                pm = self._nt("QPM")
                self.picture_mode = PICTURE_NAME.get(pm, pm)
            except (ProjectorError, OSError):
                self.picture_mode = None
        else:
            self.picture_mode = None
        temps = {}
        for key, n in (("intake", 0), ("optics", 1), ("exhaust", 2)):
            try:
                t = _parse_temp(self._nt(f"QTM:{n}"))
            except (ProjectorError, OSError):
                t = None
            if t is not None:
                temps[key] = t
        self.temps = temps

    # ---------- สั่งงาน ----------
    def control(self, action, value=None):
        """action: power(on/off) | input(HDMI...) | av_mute(on/off) | volume(up/down) | picture(STANDARD...)"""
        with self._lock:
            try:
                self._control(action, value)
            except ProjectorAuthError as e:
                raise ProjectorError(str(e))
            except OSError as e:
                raise ProjectorError(f"ติดต่อโปรเจกเตอร์ไม่ได้ ({e.__class__.__name__})")
        self._wake.set()  # ถามสถานะใหม่ทันที

    def _pj(self, cmd):
        r = pjlink_query(self.ip, self.pjlink_port, self.password, [cmd])
        v = r.get(cmd.split(" ")[0], "")
        if v.startswith("ERR"):
            raise ProjectorError(ERR_TEXT.get(v, v))

    def _control(self, action, value):
        pj, nt = self.has_pjlink, self.has_ntcontrol
        if action == "power":
            on = value == "on"
            self._pj(f"%1POWR {int(on)}") if pj else self._nt("PON" if on else "POF")
            self.power = "warming" if on else "cooling"
        elif action == "input":
            if value not in INPUTS:
                raise ProjectorError("ไม่รู้จัก input นี้")
            self._pj(f"%1INPT {INPUTS[value][0]}") if pj else self._nt(f"IIS:{INPUTS[value][1]}")
            self.input = value
        elif action == "av_mute":
            on = value == "on"
            self._pj(f"%1AVMT {'31' if on else '30'}") if pj else self._nt(f"OSH:{int(on)}")
            self.av_mute = on
        elif action == "volume":
            if value not in ("up", "down"):
                raise ProjectorError("volume ต้องเป็น up หรือ down")
            if nt:
                self._nt("AUU" if value == "up" else "AUD")
            else:
                self._pj(f"%2SVOL {1 if value == 'up' else 0}")  # PJLink class 2
        elif action == "picture":
            if value not in PICTURE_MODES:
                raise ProjectorError("ไม่รู้จัก picture mode นี้")
            if not nt:
                raise ProjectorError("เปลี่ยน picture mode ต้องใช้พอร์ตควบคุม Panasonic (1024)")
            self._nt(f"VPM:{PICTURE_MODES[value]}")
            self.picture_mode = value
        else:
            raise ProjectorError("คำสั่งไม่ถูกต้อง")

    def refresh(self):
        self.has_pjlink = self.has_ntcontrol = None
        self._extra_at = 0.0
        self._wake.set()

    def stop(self):
        self.running = False
        self._wake.set()

    def to_dict(self):
        return {
            "id": self.id, "name": self.name, "ip": self.ip, "user": self.user, "room_id": self.room_id,
            "sort_order": getattr(self, "sort_order", 0),
            "pjlink_port": self.pjlink_port, "control_port": self.control_port,
            "status": self.status, "status_text": STATUS_TEXT.get(self.status, self.status),
            "last_error": self.last_error, "power": self.power, "input": self.input,
            "av_mute": self.av_mute, "picture_mode": self.picture_mode, "lamp_hours": self.lamp_hours,
            "errors": self.errors, "temps": self.temps, "latency_ms": self.latency_ms,
            "has_pjlink": self.has_pjlink, "has_ntcontrol": self.has_ntcontrol,
            "last_online_at": self.last_online_at, "last_poll": self.last_poll,
            **{k: self.info.get(k, "") for k in ("model", "serial", "projector_name", "manufacturer", "firmware")},
        }
