"""บันทึกวิดีโอเฉพาะตอนมีการเคลื่อนไหว (คนเดิน / ภาพเปลี่ยน) ลง NAS แบบวนลูปเหมือนกล้องติดรถ

ออกแบบให้รันบน Raspberry Pi 5 (RAM 2 GB) ได้สบาย:
- ตรวจจับด้วย OpenCV แบบเบา (ย่อภาพเหลือ ~320px แล้วเทียบกับภาพพื้นหลัง) ไม่ใช้ AI
- เก็บภาพย้อนหลังก่อนเกิดเหตุ (pre-record) และบันทึกต่อหลังภาพนิ่งแล้ว (post-record)
- กล้องละ 1 thread หลุดแล้วต่อใหม่เอง
- ดิสก์ใกล้เต็ม หรือไฟล์เก่ากว่า N วัน → ลบไฟล์เก่าสุดก่อน (loop recording)

ไฟล์ที่ได้:  <output_dir>/<ชื่อกล้อง>/<YYYY-MM-DD>/<HHMMSS>.mp4

ใช้งาน:
    python3 motion_recorder.py --config motion_config.json
    python3 motion_recorder.py --config motion_config.json --show      # เปิดหน้าต่างดูกรอบที่ตรวจจับ (ต้องมีจอ / VNC)

รหัสผ่านกล้อง: ใส่ใน URL ของ config หรือใช้ {ENV:ชื่อตัวแปร} เช่น
    "url": "rtsp://admin:{ENV:CAM1_PASS}@192.168.1.108:554/cam/realmonitor?channel=1&subtype=1"
"""
import argparse
import collections
import json
import logging
import os
import re
import shutil
import signal
import threading
import time
from datetime import datetime

import cv2

os.environ.setdefault("OPENCV_FFMPEG_CAPTURE_OPTIONS", "rtsp_transport;tcp")  # RTSP ผ่าน TCP ภาพไม่แตก

log = logging.getLogger("motion")
stop_event = threading.Event()

DEFAULTS = {
    "output_dir": "/mnt/nas/recordings",
    "detect_width": 320,          # ย่อภาพก่อนตรวจจับ (ยิ่งเล็กยิ่งเร็ว)
    "fps": 10,                    # อ่าน / บันทึกกี่ภาพต่อวินาที (ประหยัด CPU)
    "min_area_ratio": 0.005,      # พื้นที่ที่เปลี่ยนต้องมากกว่ากี่ % ของภาพ (0.005 = 0.5%) ถึงนับว่าเคลื่อนไหว
    "pixel_threshold": 25,        # ความต่างของสีต่อพิกเซลที่นับว่า "เปลี่ยน" (กันแสงกระพริบเล็กน้อย)
    "trigger_frames": 3,          # ต้องเจอการเคลื่อนไหวติดกันกี่ภาพถึงเริ่มบันทึก (กันสัญญาณหลอก)
    "pre_seconds": 3,             # เก็บภาพก่อนเกิดเหตุ
    "post_seconds": 5,            # บันทึกต่อหลังภาพนิ่งแล้ว
    "max_clip_seconds": 300,      # คลิปยาวสุด แล้วตัดเป็นไฟล์ใหม่
    "keep_days": 7,               # ลบไฟล์ที่เก่ากว่านี้
    "min_free_percent": 10,       # พื้นที่ว่างต่ำกว่านี้ → ลบไฟล์เก่าสุดจนพอ
    "draw_boxes": True,           # วาดกรอบรอบจุดที่เคลื่อนไหวลงในวิดีโอ
    "timestamp": True,            # ประทับเวลาบนวิดีโอ
}


def expand_env(text):
    """แทน {ENV:NAME} ด้วยค่าตัวแปรสภาพแวดล้อม (ไม่ต้องเก็บรหัสผ่านไว้ในไฟล์)"""
    return re.sub(r"\{ENV:([A-Za-z_][A-Za-z0-9_]*)\}", lambda m: os.environ.get(m.group(1), ""), text)


def safe_name(name):
    return re.sub(r"[^\w\-]+", "_", name, flags=re.UNICODE).strip("_") or "camera"


def hide_password(url):
    return re.sub(r"//([^:/@]+):[^@]*@", r"//\1:****@", url)


class MotionDetector:
    """เทียบภาพปัจจุบันกับภาพพื้นหลังที่ค่อย ๆ ปรับตามแสง (running average)"""

    def __init__(self, width, min_area_ratio, pixel_threshold):
        self.width = width
        self.min_area_ratio = min_area_ratio
        self.pixel_threshold = pixel_threshold
        self.background = None
        self.scale = 1.0

    def detect(self, frame):
        """คืน (มีการเคลื่อนไหวไหม, รายการกรอบ [(x,y,w,h)] ในพิกัดภาพเต็ม)"""
        h, w = frame.shape[:2]
        self.scale = w / self.width
        small = cv2.resize(frame, (self.width, max(1, int(h / self.scale))), interpolation=cv2.INTER_AREA)
        gray = cv2.GaussianBlur(cv2.cvtColor(small, cv2.COLOR_BGR2GRAY), (21, 21), 0)
        if self.background is None:
            self.background = gray.astype("float")
            return False, []
        cv2.accumulateWeighted(gray, self.background, 0.05)  # พื้นหลังปรับตามแสงช้า ๆ
        diff = cv2.absdiff(gray, cv2.convertScaleAbs(self.background))
        mask = cv2.threshold(diff, self.pixel_threshold, 255, cv2.THRESH_BINARY)[1]
        mask = cv2.dilate(mask, None, iterations=2)
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        min_area = self.min_area_ratio * gray.shape[0] * gray.shape[1]
        boxes = [cv2.boundingRect(c) for c in contours if cv2.contourArea(c) >= min_area]
        s = self.scale
        return bool(boxes), [(int(x * s), int(y * s), int(bw * s), int(bh * s)) for x, y, bw, bh in boxes]


class CameraRecorder(threading.Thread):
    def __init__(self, cam, cfg, show=False):
        super().__init__(daemon=True, name=f"cam-{cam['name']}")
        self.name_ = cam["name"]
        self.url = expand_env(cam["url"])
        self.cfg = {**cfg, **cam.get("options", {})}
        self.show = show
        self.out_dir = os.path.join(self.cfg["output_dir"], safe_name(self.name_))
        self.detector = MotionDetector(self.cfg["detect_width"], self.cfg["min_area_ratio"],
                                       self.cfg["pixel_threshold"])
        fps = self.cfg["fps"]
        self.pre_buffer = collections.deque(maxlen=max(1, int(self.cfg["pre_seconds"] * fps)))
        self.writer = None
        self.clip_path = None
        self.clip_started = 0.0
        self.last_motion = 0.0
        self.motion_streak = 0
        self.latest_view = None

    # ---------- เปิด / ปิดไฟล์วิดีโอ ----------
    def _open_clip(self, frame):
        now = datetime.now()
        day_dir = os.path.join(self.out_dir, now.strftime("%Y-%m-%d"))
        os.makedirs(day_dir, exist_ok=True)
        self.clip_path = os.path.join(day_dir, now.strftime("%H%M%S") + ".mp4")
        h, w = frame.shape[:2]
        self.writer = cv2.VideoWriter(self.clip_path, cv2.VideoWriter_fourcc(*"mp4v"), self.cfg["fps"], (w, h))
        self.clip_started = time.time()
        for old in self.pre_buffer:  # ใส่ภาพก่อนเกิดเหตุไว้ต้นคลิป
            self.writer.write(old)
        self.pre_buffer.clear()
        log.info("[%s] เริ่มบันทึก %s", self.name_, self.clip_path)

    def _close_clip(self):
        if self.writer is not None:
            self.writer.release()
            log.info("[%s] บันทึกเสร็จ %s (%.0f วินาที)", self.name_, self.clip_path, time.time() - self.clip_started)
        self.writer = None
        self.clip_path = None

    def _annotate(self, frame, boxes):
        if self.cfg["draw_boxes"]:
            for x, y, w, h in boxes:
                cv2.rectangle(frame, (x, y), (x + w, y + h), (0, 255, 0), 2)
        if self.cfg["timestamp"]:
            text = f"{self.name_}  {datetime.now():%Y-%m-%d %H:%M:%S}"
            cv2.putText(frame, text, (10, 25), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 0), 3, cv2.LINE_AA)
            cv2.putText(frame, text, (10, 25), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 1, cv2.LINE_AA)
        return frame

    # ---------- วนอ่านกล้อง ----------
    def run(self):
        period = 1.0 / self.cfg["fps"]
        while not stop_event.is_set():
            cap = cv2.VideoCapture(self.url, cv2.CAP_FFMPEG)
            if not cap.isOpened():
                log.warning("[%s] เปิดกล้องไม่ได้ %s ลองใหม่ใน 10 วินาที", self.name_, hide_password(self.url))
                stop_event.wait(10)
                continue
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            log.info("[%s] เชื่อมต่อแล้ว", self.name_)
            self.detector.background = None
            next_t = time.time()
            fails = 0
            while not stop_event.is_set():
                if not cap.grab():          # ดึงทุกเฟรมทิ้งเพื่อให้ภาพล่าสุดเสมอ แต่ถอดรหัสเฉพาะตามรอบ fps
                    fails += 1
                    if fails > 50:
                        break
                    continue
                fails = 0
                now = time.time()
                if now < next_t:
                    continue
                next_t = now + period
                ok, frame = cap.retrieve()
                if not ok or frame is None:
                    continue
                self._handle(frame, now)
            cap.release()
            self._close_clip()
            if not stop_event.is_set():
                log.warning("[%s] สัญญาณหลุด ต่อใหม่ใน 5 วินาที", self.name_)
                stop_event.wait(5)
        self._close_clip()

    def _handle(self, frame, now):
        moving, boxes = self.detector.detect(frame)
        self.motion_streak = self.motion_streak + 1 if moving else 0
        if self.motion_streak >= self.cfg["trigger_frames"]:
            self.last_motion = now
        frame = self._annotate(frame, boxes)
        if self.show:
            self.latest_view = frame

        recording_wanted = now - self.last_motion <= self.cfg["post_seconds"]
        if self.writer is None:
            if recording_wanted and self.last_motion:
                self._open_clip(frame)
            else:
                self.pre_buffer.append(frame)
                return
        self.writer.write(frame)
        if not recording_wanted or now - self.clip_started >= self.cfg["max_clip_seconds"]:
            self._close_clip()


class Cleaner(threading.Thread):
    """ลบไฟล์เก่า: เกิน keep_days หรือพื้นที่ว่างเหลือน้อยกว่า min_free_percent (loop recording)"""

    def __init__(self, cfg):
        super().__init__(daemon=True, name="cleaner")
        self.root = cfg["output_dir"]
        self.keep_days = cfg["keep_days"]
        self.min_free = cfg["min_free_percent"]

    def _files(self):
        out = []
        for dirpath, _, names in os.walk(self.root):
            for n in names:
                if n.endswith(".mp4"):
                    p = os.path.join(dirpath, n)
                    try:
                        out.append((os.path.getmtime(p), p))
                    except OSError:
                        pass
        return sorted(out)

    def _free_percent(self):
        u = shutil.disk_usage(self.root)
        return u.free * 100 / u.total

    def clean_once(self):
        if not os.path.isdir(self.root):
            return 0
        removed = 0
        cutoff = time.time() - self.keep_days * 86400
        files = self._files()
        for mtime, p in files:
            if mtime < cutoff:
                removed += self._remove(p)
        files = [f for f in files if os.path.exists(f[1])]
        while files and self._free_percent() < self.min_free:
            removed += self._remove(files.pop(0)[1])   # เก่าสุดออกก่อน
        # ลบโฟลเดอร์วันที่ว่างแล้ว
        for dirpath, dirs, names in os.walk(self.root, topdown=False):
            if dirpath != self.root and not dirs and not names:
                try:
                    os.rmdir(dirpath)
                except OSError:
                    pass
        return removed

    def _remove(self, path):
        try:
            os.remove(path)
            log.info("[cleaner] ลบไฟล์เก่า %s", path)
            return 1
        except OSError:
            return 0

    def run(self):
        while not stop_event.is_set():
            try:
                self.clean_once()
            except Exception as e:  # noqa: BLE001 ไม่ให้ thread ตาย
                log.warning("[cleaner] %s", e)
            stop_event.wait(60)


def load_config(path):
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)
    cfg = {**DEFAULTS, **{k: v for k, v in raw.items() if k != "cameras"}}
    cams = [c for c in raw.get("cameras", []) if c.get("enabled", True)]
    if not cams:
        raise SystemExit("ไม่มีกล้องใน config (cameras)")
    return cfg, cams


def main():
    ap = argparse.ArgumentParser(description="บันทึกวิดีโอเมื่อมีการเคลื่อนไหว ลง NAS แบบวนลูป")
    ap.add_argument("--config", default="motion_config.json")
    ap.add_argument("--show", action="store_true", help="แสดงหน้าต่างภาพ (ต้องมีจอ / VNC)")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")

    cfg, cams = load_config(args.config)
    os.makedirs(cfg["output_dir"], exist_ok=True)
    signal.signal(signal.SIGTERM, lambda *_: stop_event.set())

    Cleaner(cfg).start()
    workers = [CameraRecorder(c, cfg, show=args.show) for c in cams]
    for w in workers:
        w.start()
    log.info("เริ่มตรวจจับ %d กล้อง บันทึกที่ %s (กด Ctrl+C เพื่อหยุด)", len(workers), cfg["output_dir"])
    try:
        while not stop_event.is_set():
            if args.show:
                for w in workers:
                    if w.latest_view is not None:
                        cv2.imshow(w.name_, w.latest_view)
                if cv2.waitKey(100) & 0xFF == ord("q"):
                    break
            else:
                stop_event.wait(1)
    except KeyboardInterrupt:
        pass
    stop_event.set()
    for w in workers:
        w.join(timeout=5)
    cv2.destroyAllWindows()
    log.info("หยุดแล้ว")


if __name__ == "__main__":
    main()
