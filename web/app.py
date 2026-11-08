"""เว็บดูกล้อง IP แบบเรียลไทม์ (ไม่บันทึกภาพ/ไม่เก็บประวัติ)

- เพิ่มกล้องด้วย IP + username + password ผ่านหน้าเว็บ
- ดึงภาพสดผ่าน RTSP แล้วส่งต่อให้เบราว์เซอร์เป็น MJPEG / JPEG
- ดึงสถานะ: ออนไลน์/ออฟไลน์, รหัสผิด, ping, FPS, ความละเอียด, รุ่น, Serial, Firmware
- ข้อมูลกล้องทั้งหมด (รวมรหัสผ่าน) อยู่ในหน่วยความจำเท่านั้น ปิดโปรแกรมแล้วหายหมด

วิธีรัน:
    pip install flask opencv-python requests
    python web/app.py                 # เปิด http://127.0.0.1:5000 (เฉพาะเครื่องนี้)
    python web/app.py --host 0.0.0.0  # ให้เครื่องอื่นในวงแลนเปิดดูได้ด้วย
"""
import argparse
import os
import re
import socket
import select
import ssl
import threading
import time
import uuid
from urllib.parse import quote, urlparse

os.environ.setdefault("OPENCV_FFMPEG_CAPTURE_OPTIONS", "rtsp_transport;tcp")
import cv2  # noqa: E402
import numpy as np  # noqa: E402
import requests  # noqa: E402
try:
    import av  # PyAV: ส่งวิดีโอ H.264 จากกล้องให้เบราว์เซอร์ตรง ๆ ไม่ต้องแปลงเป็น JPEG
except ImportError:  # ยังไม่ได้ pip install av → ใช้ MJPEG อย่างเดียว
    av = None
from requests.auth import HTTPDigestAuth, HTTPBasicAuth  # noqa: E402
from flask import Flask, Response, jsonify, request, send_from_directory, abort  # noqa: E402
import db  # noqa: E402  ฐานข้อมูลกล้อง (SQLite + เข้ารหัสรหัสผ่าน)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
OPEN_TIMEOUT_MS = 6000
READ_TIMEOUT_MS = 6000
JPEG_QUALITY = 92         # โหมด RTSP (ภาพย่อ/MJPEG): บีบอัด JPEG คุณภาพสูง ไม่ย่อขนาดภาพ
IDLE_ENCODE_SEC = 1.0     # ไม่มีคนดูภาพเต็ม → อัปเดตภาพย่อวินาทีละครั้ง
LIVE_ENCODE_SEC = 1 / 25  # มีคนดูภาพเต็ม (MJPEG) → สูงสุด ~25 fps
SNAPSHOT_WORKERS = 3      # โหมดภาพนิ่ง: ยิงขอภาพพร้อมกันหลายเส้น ได้ fps มากขึ้น (กล้องรับได้สูงสุด 10)
AUTH_RETRY_SEC = 60       # รหัสผิด: รอนานหน่อยก่อนลองใหม่ กันกล้องล็อกบัญชี
CRLF = bytes([13, 10])
SOI, EOI = bytes([0xFF, 0xD8]), bytes([0xFF, 0xD9])  # จุดเริ่ม/จบของไฟล์ JPEG
START4, START3 = bytes([0, 0, 0, 1]), bytes([0, 0, 1])  # start code ของ H.264
HTTP_MODE_RTSP_RETRY_SEC = 300  # อยู่โหมด HTTP แล้ว ลอง RTSP ใหม่ทุก 5 นาที (กดปุ่ม ⟳ เพื่อลองทันที)

STATUS_TEXT = {
    "connecting": "กำลังเชื่อมต่อ",
    "online": "ออนไลน์",
    "snapshot": "ออนไลน์ (ภาพนิ่ง)",
    "mjpeg": "ออนไลน์ (MJPEG/HTTP)",
    "offline": "ออฟไลน์",
    "auth_error": "รหัสผ่านไม่ถูกต้อง",
    "stream_error": "เปิดสตรีมไม่ได้",
}

BRANDS = {
    # brand: (rtsp path main, rtsp path sub)
    "dahua": ("/cam/realmonitor?channel=1&subtype=0", "/cam/realmonitor?channel=1&subtype=1"),
    "hikvision": ("/Streaming/Channels/101", "/Streaming/Channels/102"),
}


class AuthError(Exception):
    pass


def tcp_latency(ip, port, timeout=1.5):
    """วัดเวลาเชื่อมต่อ TCP (ms) ใช้แทน ping เพราะไม่ต้องใช้สิทธิ์ admin"""
    t0 = time.perf_counter()
    try:
        with socket.create_connection((ip, port), timeout=timeout):
            return round((time.perf_counter() - t0) * 1000, 1)
    except OSError:
        return None


def http_get(url, user, password, timeout=5, session=None, auth=None, stream=False):
    """GET ด้วย Digest ก่อน (Dahua/Hikvision ใช้ Digest) ถ้าไม่ได้ค่อยลอง Basic

    ส่ง session + auth เดิมเข้ามาเพื่อใช้การเชื่อมต่อซ้ำ (keep-alive) และใช้ nonce ของ Digest ซ้ำ
    ไม่ต้องโดน 401 ก่อนทุกครั้ง → ดึงภาพนิ่งเร็วขึ้นเกือบเท่าตัว
    """
    get = session.get if session else requests.get
    r = get(url, auth=auth or HTTPDigestAuth(user, password), timeout=timeout, stream=stream)
    if r.status_code == 401 and "basic" in r.headers.get("WWW-Authenticate", "").lower():
        r = get(url, auth=HTTPBasicAuth(user, password), timeout=timeout, stream=stream)
    if r.status_code == 401:
        raise AuthError("username/password ไม่ถูกต้อง")
    r.raise_for_status()
    return r


def parse_kv(text):
    out = {}
    for line in text.splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def fetch_info(cam):
    """ดึงข้อมูลเครื่อง (รุ่น / Serial / Firmware / ชื่อ) จาก HTTP API ของกล้อง"""
    base = f"http://{cam.ip}:{cam.http_port}"
    info = {}
    if cam.brand == "dahua":
        sysinfo = parse_kv(http_get(f"{base}/cgi-bin/magicBox.cgi?action=getSystemInfo",
                                    cam.user, cam.password).text)
        info["model"] = sysinfo.get("deviceType") or sysinfo.get("updateSerial", "")
        info["serial"] = sysinfo.get("serialNumber", "")
        for path, key, field in (("magicBox.cgi?action=getSoftwareVersion", "version", "firmware"),
                                 ("magicBox.cgi?action=getMachineName", "name", "device_name")):
            try:
                info[field] = parse_kv(http_get(f"{base}/cgi-bin/{path}",
                                                cam.user, cam.password).text).get(key, "")
            except AuthError:
                raise
            except Exception:
                pass
    elif cam.brand == "hikvision":
        xml = http_get(f"{base}/ISAPI/System/deviceInfo", cam.user, cam.password).text

        def tag(name):
            m = re.search(rf"<{name}>(.*?)</{name}>", xml)
            return m.group(1) if m else ""
        info = {"model": tag("model"), "serial": tag("serialNumber"),
                "firmware": tag("firmwareVersion"), "device_name": tag("deviceName")}
    return info


def snapshot_url(cam):
    if cam.brand == "dahua":
        return f"http://{cam.ip}:{cam.http_port}/cgi-bin/snapshot.cgi?channel=1"
    if cam.brand == "hikvision":
        return f"http://{cam.ip}:{cam.http_port}/ISAPI/Streaming/channels/101/picture"
    return None


def http_snapshot_bytes(cam, session=None, auth=None):
    """ดึงภาพนิ่งเป็นไฟล์ JPEG ดิบ (ไม่ถอดรหัส ส่งต่อให้เบราว์เซอร์ได้เลย)"""
    url = snapshot_url(cam)
    if not url:
        return None
    data = http_get(url, cam.user, cam.password, timeout=8,
                    session=session or getattr(cam, "session", None),
                    auth=auth or getattr(cam, "http_auth", None)).content
    return data if data.startswith(SOI) else None


def jpeg_size(data):
    """อ่านความกว้าง x สูงจากหัวไฟล์ JPEG (เร็วกว่าถอดรหัสทั้งภาพ)"""
    i, n = 2, len(data)
    while i + 9 < n:
        if data[i] != 0xFF:
            i += 1
            continue
        marker = data[i + 1]
        if marker in (0xC0, 0xC1, 0xC2):
            h = int.from_bytes(data[i + 5:i + 7], "big")
            w = int.from_bytes(data[i + 7:i + 9], "big")
            return w, h
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
            i += 2
            continue
        i += 2 + int.from_bytes(data[i + 2:i + 4], "big")
    return None


def avc_codec_string(extradata):
    """สร้างชื่อ codec เช่น avc1.4D401F จาก SPS ของ H.264 (เบราว์เซอร์ต้องใช้ตอนเปิด MediaSource)"""
    ed = bytes(extradata or b"")
    if len(ed) > 4 and ed[0] == 1:  # รูปแบบ avcC
        return "avc1." + ed[1:4].hex().upper()
    for sc in (START4, START3):
        i = ed.find(sc)
        while i >= 0:
            j = i + len(sc)
            if j + 3 < len(ed) and ed[j] & 0x1F == 7:  # NAL type 7 = SPS
                return "avc1." + ed[j + 1:j + 4].hex().upper()
            i = ed.find(sc, j)
    return "avc1.640029"  # ไม่รู้จริง ๆ ใช้ค่ากลาง (High@4.1)


class ChunkWriter:
    """ไฟล์ปลอมให้ PyAV เขียน MP4 ลงหน่วยความจำ แล้วเราดึงไปส่งเบราว์เซอร์ทีละก้อน"""
    def __init__(self):
        self.chunks = []

    def write(self, b):
        self.chunks.append(bytes(b))
        return len(b)

    def take(self):
        data, self.chunks = b"".join(self.chunks), []
        return data


def rtsp_ping(ip, port):
    # กล้อง Dahua ไม่ตอบ "OPTIONS *" ต้องใส่ URL เต็ม
    return (f"OPTIONS rtsp://{ip}:{port}/ RTSP/1.0".encode() + CRLF + b"CSeq: 1" + CRLF
            + b"User-Agent: CameraMonitor" + CRLF + CRLF)


def _rtsp_answers(sock, ip, port):
    sock.sendall(rtsp_ping(ip, port))
    return sock.recv(64).startswith(b"RTSP/1.0")


def _insecure_tls():
    # กล้องใช้ใบรับรองที่ออกเอง (self-signed) จึงไม่ตรวจใบรับรอง แต่ข้อมูลยังเข้ารหัสอยู่
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def detect_rtsp_tls(ip, port, timeout=3):
    """ตรวจว่าพอร์ต RTSP ของกล้องเป็นแบบธรรมดา (False) หรือเข้ารหัส TLS/rtsps (True) หรือใช้ไม่ได้ (None)

    กล้อง Dahua ที่เปิด "RTSP over TLS" จะตัดการเชื่อมต่อแบบธรรมดาทิ้งทันที
    """
    try:
        with socket.create_connection((ip, port), timeout=timeout) as s:
            if _rtsp_answers(s, ip, port):
                return False
    except OSError:
        pass
    try:
        with _insecure_tls().wrap_socket(socket.create_connection((ip, port), timeout=timeout)) as s:
            if _rtsp_answers(s, ip, port):
                return True
    except OSError:
        pass
    return None


class TlsProxy:
    """ตัวกลางถอด TLS: เปิดพอร์ตในเครื่อง (127.0.0.1) ให้ OpenCV/PyAV ต่อแบบ rtsp:// ธรรมดา
    แล้วส่งต่อข้อมูลไป-กลับกับกล้องผ่าน TLS (เพราะ FFmpeg บางชุดต่อ rtsps:// ไม่ได้)"""

    def __init__(self, host, port):
        self.host, self.port = host, port
        self.server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server.bind(("127.0.0.1", 0))
        self.server.listen(8)
        self.local_port = self.server.getsockname()[1]
        self.closed = False
        threading.Thread(target=self._accept_loop, daemon=True).start()

    def _accept_loop(self):
        while not self.closed:
            try:
                client, _ = self.server.accept()
            except OSError:
                return
            threading.Thread(target=self._handle, args=(client,), daemon=True).start()

    def _handle(self, client):
        try:
            remote = _insecure_tls().wrap_socket(socket.create_connection((self.host, self.port), timeout=6))
            # อ่านแบบไม่รอ: TLS 1.3 ส่งข้อมูล session มาหลังจับมือ ทำให้ select บอกว่า "อ่านได้"
            # ทั้งที่ยังไม่มีข้อมูลจริง ถ้า recv แบบรอจะค้าง (รอคำตอบที่ยังไม่ได้ส่งคำขอไป)
            remote.setblocking(False)
        except OSError:
            client.close()
            return

        def send_tls(data):
            view = memoryview(data)
            while view:
                try:
                    view = view[remote.send(view):]
                except (ssl.SSLWantWriteError, ssl.SSLWantReadError):
                    select.select([remote], [remote], [], 1.0)

        # ส่งต่อข้อมูลสองทางด้วย thread เดียว (select)
        # ห้ามใช้ socket TLS ตัวเดียวอ่าน+เขียนพร้อมกันจากสอง thread: OpenSSL ไม่รองรับ บน Windows ค้างได้
        try:
            while not self.closed:
                # remote.pending() = ข้อมูล TLS ที่ถอดรหัสแล้วค้างในบัฟเฟอร์ (select มองไม่เห็น)
                readable = [remote] if remote.pending() else select.select([client, remote], [], [], 1.0)[0]
                if remote in readable:
                    try:
                        data = remote.recv(65536)
                        if not data:
                            break
                        client.sendall(data)
                    except (ssl.SSLWantReadError, ssl.SSLWantWriteError):
                        pass  # ยังไม่มีข้อมูลวิดีโอ/คำตอบจริง รอบถัดไปค่อยอ่าน
                if client in readable:
                    data = client.recv(65536)
                    if not data:
                        break
                    send_tls(data)
        except OSError:
            pass
        finally:
            for x in (client, remote):
                try:
                    x.close()
                except OSError:
                    pass

    def close(self):
        self.closed = True
        try:
            self.server.close()
        except OSError:
            pass


class Camera:
    def __init__(self, name, ip, user, password, brand="dahua", quality="sub",
                 rtsp_url="", http_port=80, rtsp_port=554, cam_id=None):
        self.id = cam_id or uuid.uuid4().hex[:8]
        self.rtsp_input = rtsp_url
        self.name = name or ip
        self.ip = ip
        self.user = user
        self.password = password
        self.brand = brand
        self.quality = quality
        self.custom_rtsp = rtsp_url
        self.rtsp_tls = rtsp_url.lower().startswith("rtsps://")  # ตรวจซ้ำอัตโนมัติทุกครั้งที่เชื่อมต่อ
        self._tls_proxy = None
        self.http_port = http_port
        self.rtsp_port = rtsp_port

        self.status = "connecting"
        self.last_error = ""
        self.mode = ""
        self.info = {}
        self.latency_ms = None
        self.resolution = ""
        self.fps = 0.0
        self.online_since = None
        self.added_at = time.time()

        self.viewers = 0
        self.jpeg = None
        self.jpeg_time = 0.0
        self.frame_cond = threading.Condition()
        self._last_req_time = 0.0
        self.session = requests.Session()  # ใช้การเชื่อมต่อ HTTP ซ้ำ
        self.http_auth = HTTPDigestAuth(user, password)
        self.running = True
        self.wake = threading.Event()  # ถูก set เมื่อผู้ใช้กด "เชื่อมต่อใหม่"
        self.monitor_wake = threading.Event()
        threading.Thread(target=self._stream_loop, daemon=True).start()
        threading.Thread(target=self._monitor_loop, daemon=True).start()

    # ---------- URL ----------
    def _proxy_port(self, host, port):
        if not self._tls_proxy or (self._tls_proxy.host, self._tls_proxy.port) != (host, port):
            if self._tls_proxy:
                self._tls_proxy.close()
            self._tls_proxy = TlsProxy(host, port)
        return self._tls_proxy.local_port

    def rtsp_url(self, hide_password=False):
        """URL ที่ OpenCV/PyAV ใช้ต่อ ถ้ากล้องเป็น RTSP over TLS จะชี้ไปที่ตัวกลาง TLS ในเครื่องแทน
        hide_password=True: URL สำหรับแสดงบนหน้าเว็บ (ซ่อนรหัส และแสดงที่อยู่จริงของกล้อง)"""
        if self.custom_rtsp:
            u = urlparse(self.custom_rtsp)
            host, port = u.hostname, u.port or (322 if u.scheme == "rtsps" else 554)
            userinfo = u.netloc.rsplit("@", 1)[0] + "@" if "@" in u.netloc else ""
            rest = self.custom_rtsp.split(u.netloc, 1)[1]
            if hide_password:
                userinfo = re.sub(r":[^:@]*@$", ":****@", userinfo)
                return f"{'rtsps' if self.rtsp_tls else 'rtsp'}://{userinfo}{host}:{port}{rest}"
            if self.rtsp_tls:
                return f"rtsp://{userinfo}127.0.0.1:{self._proxy_port(host, port)}{rest}"
            return self.custom_rtsp.replace("rtsps://", "rtsp://", 1)
        pw = "****" if hide_password else quote(self.password, safe="")
        main, sub = BRANDS[self.brand]
        path = main if self.quality == "main" else sub
        auth = f"{quote(self.user, safe='')}:{pw}@"
        if hide_password:
            return f"{'rtsps' if self.rtsp_tls else 'rtsp'}://{auth}{self.ip}:{self.rtsp_port}{path}"
        if self.rtsp_tls:
            return f"rtsp://{auth}127.0.0.1:{self._proxy_port(self.ip, self.rtsp_port)}{path}"
        return f"rtsp://{auth}{self.ip}:{self.rtsp_port}{path}"

    # ---------- สถานะ ----------
    def _set_status(self, status, error=""):
        if status in ("online", "snapshot", "mjpeg"):
            if self.online_since is None:
                self.online_since = time.time()
        else:
            self.online_since = None
            self.fps = 0.0
        changed = (status, error, self.mode, self.rtsp_tls) != getattr(self, "_saved_state", None)
        self.status = status
        self.last_error = error
        if changed:  # บันทึกลงฐานข้อมูลเฉพาะตอนสถานะเปลี่ยน
            self._saved_state = (status, error, self.mode, self.rtsp_tls)
            fields = dict(last_status=status, last_error=error, last_mode=self.mode, rtsp_tls=int(self.rtsp_tls))
            if self.online_since or getattr(self, "_was_online", False):  # ออนไลน์อยู่ หรือเพิ่งหลุด
                self.last_online_at = fields["last_online_at"] = time.time()
            self._was_online = bool(self.online_since)
            db.update_runtime(self.id, **fields)

    def _monitor_loop(self):
        """ทุก 5 วินาที: วัด ping (TCP) และดึงข้อมูลเครื่องถ้ายังไม่มี"""
        while self.running:
            self.latency_ms = tcp_latency(self.ip, self.http_port) or tcp_latency(self.ip, self.rtsp_port)
            if self.latency_ms is None and self.status != "connecting" \
                    and time.time() - self.jpeg_time > 10:
                self._set_status("offline", "ติดต่อกล้องไม่ได้ (ไม่ตอบสนอง)")
            if self.latency_ms is not None and not self.info and self.brand in BRANDS \
                    and self.status != "auth_error":
                try:
                    self.info = fetch_info(self)
                    db.update_runtime(self.id, **{k: self.info.get(k, "") for k in
                                                  ("model", "serial", "firmware", "device_name")})
                except AuthError as e:
                    self._set_status("auth_error", str(e))
                except Exception:
                    pass
            for _ in range(50):
                if not self.running:
                    return
                if self.monitor_wake.is_set():
                    self.monitor_wake.clear()
                    break
                time.sleep(0.1)

    # ---------- ภาพ ----------
    def _publish(self, frame):
        h, w = frame.shape[:2]
        self.resolution = f"{w}x{h}"
        # ความละเอียดเท่าต้นฉบับจากกล้อง ไม่ย่อขนาด
        ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY])
        if ok:
            with self.frame_cond:
                self.jpeg = buf.tobytes()
                self.jpeg_time = time.time()
                self.frame_cond.notify_all()

    def _publish_jpeg(self, data, req_time=None):
        """ส่งไฟล์ JPEG จากกล้องต่อให้เบราว์เซอร์ตรง ๆ ไม่ถอดรหัส/ไม่บีบอัดใหม่ → เร็วและไม่เปลือง CPU

        req_time: เวลาที่เริ่มขอภาพ ใช้ทิ้งภาพที่มาถึงช้ากว่าภาพใหม่กว่า (ตอนขอพร้อมกันหลายเส้น)
        """
        with self.frame_cond:
            if req_time is not None and req_time < self._last_req_time:
                return False
            if req_time is not None:
                self._last_req_time = req_time
            self.jpeg = data
            self.jpeg_time = time.time()
            self.frame_cond.notify_all()
        if not self.resolution:
            size = jpeg_size(data)
            if size:
                self.resolution = f"{size[0]}x{size[1]}"
        return True

    def _need_frame(self):
        gap = LIVE_ENCODE_SEC if self.viewers > 0 else IDLE_ENCODE_SEC
        return time.time() - self.jpeg_time >= gap

    def _sleep(self, sec):
        end = time.time() + sec
        while self.running and not self.wake.is_set() and time.time() < end:
            time.sleep(0.2)

    def _stream_loop(self):
        while self.running:
            self.wake.clear()
            # ตรวจว่า RTSP ของกล้องเป็นแบบธรรมดาหรือแบบเข้ารหัส (rtsps) ทุกครั้งก่อนเชื่อมต่อ
            if self.custom_rtsp:
                u = urlparse(self.custom_rtsp)
                tls = detect_rtsp_tls(u.hostname, u.port or 554)
            else:
                tls = detect_rtsp_tls(self.ip, self.rtsp_port)
            if tls is not None:
                self.rtsp_tls = tls
            if tcp_latency(self.ip, self.rtsp_port) is None and not self.custom_rtsp:
                # พอร์ต RTSP ปิด แต่ HTTP ยังใช้ได้ → ดึงภาพผ่าน HTTP แทน
                if tcp_latency(self.ip, self.http_port) is not None and self._try_snapshot_mode():
                    continue
                if self.status != "auth_error":
                    self._set_status("offline", "ติดต่อพอร์ต RTSP และ HTTP ไม่ได้")
                self._sleep(AUTH_RETRY_SEC if self.status == "auth_error" else 3)
                continue

            cap = cv2.VideoCapture(self.rtsp_url(), cv2.CAP_FFMPEG,
                                   [cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, OPEN_TIMEOUT_MS,
                                    cv2.CAP_PROP_READ_TIMEOUT_MSEC, READ_TIMEOUT_MS])
            if not cap.isOpened():
                cap.release()
                if self._try_snapshot_mode():
                    continue
                if self.status == "auth_error":
                    self._sleep(AUTH_RETRY_SEC)
                else:
                    self._sleep(5)
                continue

            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)  # ลดดีเลย์
            self.mode = "rtsp"
            self.resolution = ""
            self._set_status("online")
            frames, t0, fails = 0, time.time(), 0
            while self.running and not self.wake.is_set():
                if not cap.grab():
                    fails += 1
                    if fails >= 3:
                        break
                    continue
                fails = 0
                frames += 1
                now = time.time()
                if now - t0 >= 2:
                    self.fps = round(frames / (now - t0), 1)
                    frames, t0 = 0, now
                if self._need_frame():
                    ok, frame = cap.retrieve()
                    if ok and frame is not None:
                        self._publish(frame)
            cap.release()
            if self.running and not self.wake.is_set():
                self._set_status("offline", "สัญญาณภาพหลุด กำลังเชื่อมต่อใหม่")
                self._sleep(2)

    def _try_snapshot_mode(self):
        """RTSP ใช้ไม่ได้ → ดึงภาพผ่าน HTTP (พอร์ต 80) แทน

        ลำดับ: 1) วิดีโอ MJPEG ผ่าน HTTP (ลื่นเกือบเท่า RTSP ถ้ากล้องตั้ง Sub stream เป็น MJPEG)
               2) ดึงภาพนิ่งต่อเนื่อง (snapshot) ยิงพร้อมกันหลายเส้น ใช้ได้กับกล้องแทบทุกตัว
        """
        if self.brand not in BRANDS:
            self._set_status("stream_error", "เปิด RTSP URL ไม่ได้")
            return False
        try:
            data = http_snapshot_bytes(self)
        except AuthError as e:
            self._set_status("auth_error", str(e))
            return False
        except Exception as e:
            self._set_status("stream_error", f"เปิด RTSP ไม่ได้ และดึงภาพนิ่งไม่ได้: {e.__class__.__name__}")
            return False
        if not data:
            self._set_status("stream_error", "เปิด RTSP ไม่ได้")
            return False
        self.resolution = ""
        self._publish_jpeg(data)

        deadline = time.time() + HTTP_MODE_RTSP_RETRY_SEC
        if self.brand == "dahua" and self._mjpeg_loop(deadline):
            return True
        self._snapshot_loop(deadline)
        return True  # ครบเวลา หรือกด ⟳ → กลับไปลอง RTSP ใหม่

    def _active(self, deadline):
        return self.running and not self.wake.is_set() and time.time() < deadline

    def _mjpeg_loop(self, deadline):
        """อ่านวิดีโอ MJPEG จาก /cgi-bin/mjpg/video.cgi คืน False ถ้ากล้องไม่รองรับ

        ภาพแต่ละเฟรมเป็น JPEG อยู่แล้ว → ส่งต่อทันทีทุกเฟรม ไม่ถอดรหัส
        """
        sub = 0 if self.quality == "main" else 1
        url = f"http://{self.ip}:{self.http_port}/cgi-bin/mjpg/video.cgi?channel=1&subtype={sub}"
        try:
            r = http_get(url, self.user, self.password, timeout=(4, 5),
                         session=self.session, auth=self.http_auth, stream=True)
        except Exception:
            return False
        if "multipart" not in r.headers.get("Content-Type", "").lower():
            r.close()
            return False

        buf, got_frame, frames, t0 = b"", False, 0, time.time()
        last_frame = time.time()
        self.resolution = ""
        try:
            for chunk in r.iter_content(chunk_size=None):  # ส่งต่อทันทีที่ข้อมูลมาถึง
                if not self._active(deadline):
                    break
                # กล้องบางรุ่นเปิดการเชื่อมต่อค้างไว้แต่ไม่ส่งภาพ (เช่น Sub stream เป็น H.264)
                # → ไม่มีภาพใหม่เกิน 5 วินาที ให้เลิกแล้วไปใช้โหมดภาพนิ่งแทน
                if time.time() - last_frame > 5:
                    break
                buf += chunk
                while True:
                    a = buf.find(SOI)
                    b = buf.find(EOI, a + 2) if a >= 0 else -1
                    if a < 0 or b < 0:
                        if len(buf) > 8_000_000:  # กันหน่วยความจำบวม
                            buf = b""
                        break
                    jpg, buf = buf[a:b + 2], buf[b + 2:]
                    frames += 1
                    last_frame = time.time()
                    if not got_frame:
                        got_frame = True
                        self.mode = "mjpeg"
                        self._set_status("mjpeg", "RTSP ใช้ไม่ได้ ใช้วิดีโอ MJPEG ผ่าน HTTP แทน")
                    self._publish_jpeg(jpg)
                    now = time.time()
                    if now - t0 >= 2:
                        self.fps = round(frames / (now - t0), 1)
                        frames, t0 = 0, now
        except Exception:
            pass
        finally:
            r.close()
        return got_frame

    def _snapshot_loop(self, deadline):
        """ดึงภาพนิ่งต่อเนื่อง

        มีคนดูภาพสด → ยิงขอพร้อมกัน SNAPSHOT_WORKERS เส้น (กล้องสร้างภาพไปพร้อมกันได้ fps สูงขึ้น)
        ไม่มีคนดู   → เส้นเดียว ทุก 2 วินาที (แค่อัปเดตภาพย่อ)
        """
        self.mode = "snapshot"
        self._set_status("snapshot", "RTSP ใช้ไม่ได้ ใช้ภาพนิ่งผ่าน HTTP แทน (ตั้ง Sub stream เป็น MJPEG จะลื่นกว่า)")
        state = {"frames": 0, "errors": 0}
        lock = threading.Lock()

        def worker(idx):
            sess, auth = requests.Session(), HTTPDigestAuth(self.user, self.password)
            while self._active(deadline) and state["errors"] < 5:
                if idx > 0 and self.viewers == 0:  # เส้นเสริมทำงานเฉพาะตอนมีคนดู
                    sess.close()  # ปล่อยการเชื่อมต่อคืนกล้อง (กล้องรับได้จำกัด เช่น 10 การเชื่อมต่อ)
                    time.sleep(0.2)
                    continue
                t = time.time()
                try:
                    data = http_snapshot_bytes(self, sess, auth)
                    with lock:
                        state["errors"] = 0
                    if data and self._publish_jpeg(data, req_time=t):
                        with lock:
                            state["frames"] += 1
                except Exception:
                    with lock:
                        state["errors"] += 1
                    time.sleep(0.5)
                if idx == 0 and self.viewers == 0:
                    self._sleep(2)
            sess.close()

        threads = [threading.Thread(target=worker, args=(i,), daemon=True) for i in range(SNAPSHOT_WORKERS)]
        for th in threads:
            th.start()
        t0 = time.time()
        while self._active(deadline) and state["errors"] < 5:
            time.sleep(0.5)
            now = time.time()
            if now - t0 >= 2:
                with lock:
                    self.fps, state["frames"] = round(state["frames"] / (now - t0), 1), 0
                t0 = now
        for th in threads:
            th.join(timeout=10)
        if state["errors"] >= 5:
            self._set_status("offline", "ดึงภาพนิ่งไม่ได้")

    def reconnect(self):
        """รีเซ็ตการเชื่อมต่อทันที ไม่ต้องรอรอบลองใหม่ (รวมถึงรอบรอ 60 วินาทีกรณีรหัสผิด)"""
        self.info = {}  # ให้ดึงรุ่น/Serial/Firmware ใหม่ด้วย
        self._set_status("connecting")
        self.mode = ""
        self.resolution = ""
        self.wake.set()
        self.monitor_wake.set()

    def stop(self):
        self.running = False
        if self._tls_proxy:
            self._tls_proxy.close()
        self.wake.set()
        with self.frame_cond:
            self.frame_cond.notify_all()

    def to_dict(self):
        return {
            "id": self.id, "name": self.name, "ip": self.ip, "brand": self.brand,
            "quality": self.quality, "user": self.user,
            "status": self.status, "status_text": STATUS_TEXT.get(self.status, self.status),
            "last_error": self.last_error, "mode": self.mode,
            "latency_ms": self.latency_ms, "resolution": self.resolution, "fps": self.fps,
            "online_since": self.online_since, "has_frame": self.jpeg is not None,
            "frame_age": round(time.time() - self.jpeg_time, 1) if self.jpeg else None,
            "rtsp": self.rtsp_url(hide_password=True) if not self.custom_rtsp else "(กำหนดเอง)",
            "viewers": self.viewers,
            "last_online_at": getattr(self, "last_online_at", None),
            "http_port": self.http_port, "rtsp_port": self.rtsp_port,
            # URL ที่ผู้ใช้กรอก (ตัด user:pass ออก ไม่ส่งรหัสผ่านกลับไปหน้าเว็บ)
            "rtsp_url": re.sub(r"//[^/@]*@", "//", self.rtsp_input or ""),
            **{k: self.info.get(k, "") for k in ("model", "serial", "firmware", "device_name")},
        }


# ====================== Web ======================
app = Flask(__name__, static_folder=None)
cameras = {}
cameras_lock = threading.Lock()


def get_cam(cam_id):
    cam = cameras.get(cam_id)
    if not cam:
        abort(404)
    return cam


@app.get("/")
def index():
    return send_from_directory(os.path.join(BASE_DIR, "static"), "index.html")


@app.get("/favicon.ico")
def favicon():
    return Response(status=204)  # ไม่มีไอคอน ไม่ต้องขึ้น 404 ใน console


@app.get("/api/cameras")
def list_cameras():
    with cameras_lock:
        cams = list(cameras.values())
    return jsonify([c.to_dict() for c in cams])


class FormError(Exception):
    pass


def build_camera(d, old=None):
    """ตรวจข้อมูลจากฟอร์มแล้วสร้างกล้องใหม่ (old = กล้องเดิมตอนแก้ไข)"""
    ip = (d.get("ip") or "").strip()
    user = (d.get("user") or "").strip()
    password = d.get("password") or ""
    if old and not password:
        password = old.password  # แก้ไขแล้วเว้นช่องรหัสว่าง = ใช้รหัสเดิม
    brand = d.get("brand") or "dahua"
    rtsp_input = (d.get("rtsp_url") or "").strip()
    rtsp_url = ""

    if brand == "other":
        if not rtsp_input.lower().startswith("rtsp://"):
            raise FormError("กล้องยี่ห้ออื่นต้องใส่ RTSP URL (ขึ้นต้นด้วย rtsp://)")
        parsed = urlparse(rtsp_input)
        ip = parsed.hostname or ip
        rtsp_url = rtsp_input
        # URL ไม่มี user:pass แต่กรอก Username/Password ไว้ → ใส่ให้อัตโนมัติ
        if old and old.custom_rtsp and rtsp_input == re.sub(r"//[^/@]*@", "//", old.rtsp_input) \
                and not d.get("password") and user == old.user:
            rtsp_url = old.custom_rtsp  # URL เดิมไม่ได้แก้ → ใช้ตัวเดิมที่มีรหัสผ่านอยู่แล้ว
        elif user and "@" not in parsed.netloc:
            rtsp_url = rtsp_input.replace(
                "rtsp://", f"rtsp://{quote(user, safe='')}:{quote(password, safe='')}@", 1)
    elif brand not in BRANDS:
        raise FormError("ไม่รู้จักยี่ห้อกล้องนี้")
    if not re.fullmatch(r"[A-Za-z0-9.\-]+", ip or ""):
        raise FormError("IP ไม่ถูกต้อง")
    if brand != "other" and not user:
        raise FormError("กรุณาใส่ username")
    with cameras_lock:
        if brand != "other" and any(c.ip == ip and c.brand != "other" and c is not old
                                    for c in cameras.values()):
            raise FormError(f"มีกล้อง {ip} อยู่ในรายการแล้ว")

    try:
        http_port = int(d.get("http_port") or 80)
        rtsp_port = int(d.get("rtsp_port") or 554)
    except ValueError:
        raise FormError("พอร์ตต้องเป็นตัวเลข")

    # ตรวจรหัสผ่านก่อนเพิ่ม (กล้องบางรุ่นล็อกบัญชีถ้าใส่รหัสผิดซ้ำ ๆ)
    info = {}
    if brand in BRANDS:
        probe = Camera.__new__(Camera)
        probe.ip, probe.user, probe.password, probe.brand, probe.http_port = ip, user, password, brand, http_port
        if tcp_latency(ip, http_port) is None and tcp_latency(ip, rtsp_port) is None:
            raise FormError(f"ติดต่อ {ip} ไม่ได้ ตรวจสอบ IP และสายแลน")
        try:
            info = fetch_info(probe)
        except AuthError:
            raise FormError("username หรือ password ไม่ถูกต้อง")
        except Exception:
            info = {}  # API ข้อมูลเครื่องใช้ไม่ได้ ไม่เป็นไร ยังลองดูภาพได้

    cam = Camera(d.get("name", "").strip(), ip, user, password, brand,
                 d.get("quality") or "sub", rtsp_url, http_port, rtsp_port,
                 cam_id=old.id if old else None)
    cam.rtsp_input = rtsp_input
    cam.info = info
    return cam


@app.post("/api/cameras")
def add_camera():
    try:
        cam = build_camera(request.get_json(force=True) or {})
    except FormError as e:
        return jsonify(error=str(e)), 400
    with cameras_lock:
        cameras[cam.id] = cam
    db.save_camera(cam)
    db.update_runtime(cam.id, last_status=cam.status, last_error=cam.last_error, last_mode=cam.mode)
    return jsonify(cam.to_dict()), 201


@app.put("/api/cameras/<cam_id>")
def edit_camera(cam_id):
    old = get_cam(cam_id)
    try:
        cam = build_camera(request.get_json(force=True) or {}, old=old)
    except FormError as e:
        return jsonify(error=str(e)), 400
    old.stop()  # หยุดการเชื่อมต่อเดิม แล้วเริ่มใหม่ด้วยค่าใหม่ (id เดิม)
    with cameras_lock:
        cameras[cam.id] = cam
    db.save_camera(cam)
    db.update_runtime(cam.id, last_status=cam.status, last_error=cam.last_error, last_mode=cam.mode)
    return jsonify(cam.to_dict())


@app.delete("/api/cameras/<cam_id>")
def delete_camera(cam_id):
    with cameras_lock:
        cam = cameras.pop(cam_id, None)
    if not cam:
        abort(404)
    db.delete_camera(cam_id)
    cam.stop()
    return jsonify(ok=True)


@app.post("/api/cameras/<cam_id>/reconnect")
def reconnect_camera(cam_id):
    get_cam(cam_id).reconnect()
    return jsonify(ok=True)


@app.post("/api/cameras/reconnect")
def reconnect_all():
    with cameras_lock:
        cams = list(cameras.values())
    for c in cams:
        c.reconnect()
    return jsonify(ok=True, count=len(cams))


@app.get("/api/cameras/<cam_id>/snapshot.jpg")
def snapshot(cam_id):
    """ภาพล่าสุด ?w=320 = ย่อเป็นภาพย่อสำหรับตาราง (เล็กกว่าเต็มภาพ ~20 เท่า หน้าเว็บไม่หน่วงแม้มีหลายกล้อง)"""
    cam = get_cam(cam_id)
    if not cam.jpeg:
        abort(404)
    data = cam.jpeg
    w = request.args.get("w", type=int)
    if w and 32 <= w < 1920:
        key = (cam.jpeg_time, w)
        if getattr(cam, "_thumb_key", None) != key:  # ย่อครั้งเดียวต่อภาพ ใช้ซ้ำได้
            img = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_REDUCED_COLOR_4 if w <= 480 else cv2.IMREAD_REDUCED_COLOR_2)  # ถอดรหัสแบบย่อ เร็วกว่า
            if img is not None:
                h0, w0 = img.shape[:2]
                if w0 > w:
                    img = cv2.resize(img, (w, int(h0 * w / w0)), interpolation=cv2.INTER_AREA)
                ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 70])
                if ok:
                    cam._thumb, cam._thumb_key = buf.tobytes(), key
        data = getattr(cam, "_thumb", None) or data
    return Response(data, mimetype="image/jpeg", headers={"Cache-Control": "no-store"})


@app.get("/api/cameras/<cam_id>/live.mp4")
def live_mp4(cam_id):
    """ส่งวิดีโอ H.264 จาก RTSP ให้เบราว์เซอร์ตรง ๆ (ห่อเป็น fragmented MP4 สำหรับ MediaSource)

    ไม่ถอดรหัส ไม่บีบอัดใหม่ → ลื่นเท่าหน้าเว็บของกล้อง (25 fps) และแทบไม่กิน CPU
    ใช้ได้เฉพาะตอน RTSP ใช้ได้และกล้องส่ง H.264 (H.265 เบราว์เซอร์ส่วนใหญ่เล่นไม่ได้ → ใช้ MJPEG แทน)
    """
    cam = get_cam(cam_id)
    if av is None:
        return jsonify(error="ยังไม่ได้ติดตั้ง PyAV (pip install av)"), 501
    if cam.mode != "rtsp":
        return jsonify(error="กล้องยังไม่ได้เชื่อมผ่าน RTSP"), 409
    try:
        inp = av.open(cam.rtsp_url(), timeout=(OPEN_TIMEOUT_MS / 1000, READ_TIMEOUT_MS / 1000),
                      options={"rtsp_transport": "tcp", "flags": "low_delay",
                               # ไม่ใช้ fflags=nobuffer: ทำให้เฟรมแรกเสียสถานะ keyframe ต้องรอ keyframe ถัดไป
                               "analyzeduration": "1000000", "probesize": "500000"})
    except Exception as e:
        return jsonify(error=f"เปิด RTSP ไม่ได้: {e.__class__.__name__}"), 502
    vs = inp.streams.video[0] if inp.streams.video else None
    if vs is None or vs.codec_context.name != "h264":
        name = vs.codec_context.name if vs else "ไม่มีวิดีโอ"
        inp.close()
        return jsonify(error=f"codec {name} เล่นในเบราว์เซอร์ตรง ๆ ไม่ได้"), 415
    codec = avc_codec_string(vs.codec_context.extradata)

    def gen():
        writer = ChunkWriter()
        out = av.open(writer, "w", format="mp4",
                      options={"movflags": "empty_moov+default_base_moof+frag_every_frame",
                               "flush_packets": "1"},  # ส่งออกทุกเฟรม ไม่รอเต็มบัฟเฟอร์
                      buffer_size=4096)
        ostream = out.add_stream_from_template(vs)
        started = False
        try:
            for pkt in inp.demux(vs):
                if not cam.running:
                    break
                if pkt.size == 0:
                    continue
                if pkt.dts is None:  # เฟรมแรกของสตรีมมักไม่มี dts → ใช้ pts แทน (ไม่งั้นจะทิ้ง keyframe แรก)
                    if pkt.pts is None:
                        continue
                    pkt.dts = pkt.pts
                if not started:  # เริ่มส่งที่ keyframe เสมอ ไม่งั้นภาพแรก ๆ จะเป็นสีเทา
                    if not pkt.is_keyframe:
                        continue
                    started = True
                pkt.stream = ostream
                out.mux(pkt)
                data = writer.take()
                if data:
                    yield data
        except Exception:
            pass
        finally:
            for c in (out, inp):
                try:
                    c.close()
                except Exception:
                    pass

    return Response(gen(), mimetype="video/mp4",
                    headers={"X-Codec": codec, "Cache-Control": "no-store",
                             "Access-Control-Expose-Headers": "X-Codec"})


@app.get("/api/cameras/<cam_id>/stream.mjpg")
def stream(cam_id):
    cam = get_cam(cam_id)

    def gen():
        cam.viewers += 1
        last = 0.0
        try:
            while cam.running:
                with cam.frame_cond:
                    cam.frame_cond.wait_for(lambda: cam.jpeg_time != last or not cam.running, timeout=5)
                    data, last = cam.jpeg, cam.jpeg_time
                if data:
                    yield (b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: "
                           + str(len(data)).encode() + b"\r\n\r\n" + data + b"\r\n")
        finally:
            cam.viewers -= 1

    return Response(gen(), mimetype="multipart/x-mixed-replace; boundary=frame",
                    headers={"Cache-Control": "no-store"})


def load_saved_cameras():
    """โหลดกล้องที่บันทึกไว้ในฐานข้อมูล แล้วเริ่มเชื่อมต่อทุกตัว (ไม่ต้องเพิ่มใหม่หลังรีสตาร์ท)"""
    db.init()
    for row in db.load_all():
        cam = Camera(row["name"], row["ip"], row["username"], row["password"], row["brand"],
                     row["quality"], row["rtsp_url"], row["http_port"], row["rtsp_port"], cam_id=row["id"])
        cam.rtsp_input = row["rtsp_url"]
        cam.info = {k: row[k] for k in ("model", "serial", "firmware", "device_name") if row[k]}
        cam.last_online_at = row["last_online_at"]
        with cameras_lock:
            cameras[cam.id] = cam
    print(f"โหลดกล้องจากฐานข้อมูล {len(cameras)} ตัว ({db.DB_PATH})")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=5000)
    args = ap.parse_args()
    load_saved_cameras()
    print(f"เปิดเบราว์เซอร์ที่ http://{'127.0.0.1' if args.host == '0.0.0.0' else args.host}:{args.port}")
    app.run(host=args.host, port=args.port, threaded=True, debug=False)
