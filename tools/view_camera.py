"""ดูภาพสดจากกล้อง Dahua (ค่าเริ่มต้น 192.168.1.108) ด้วย Python + OpenCV

วิธีใช้:
    pip install opencv-python
    python tools/view_camera.py                      # IP 192.168.1.108, user admin
    python tools/view_camera.py 192.168.1.108 admin 0  # subtype 0 = ภาพชัด (main), 1 = ภาพเล็ก (sub)

รหัสผ่าน: ตั้งตัวแปร CAM_PASS ไว้ก่อน หรือปล่อยว่างแล้วสคริปต์จะถาม (พิมพ์แล้วไม่แสดง)

ปุ่มลัดในหน้าต่างภาพ:
    q / Esc  ออก
    s        บันทึกภาพปัจจุบันเป็นไฟล์ .jpg

ถ้าเปิด RTSP (พอร์ต 554) ไม่ได้ จะสลับไปดึงภาพนิ่งผ่าน HTTP (snapshot.cgi) ต่อเนื่องแทน
"""
import os
import sys
import time
import getpass
import urllib.request
from datetime import datetime
from urllib.parse import quote

# ใช้ TCP สำหรับ RTSP ภาพจะไม่แตก/ไม่ค้างเหมือน UDP และตั้ง timeout 5 วินาที
os.environ.setdefault("OPENCV_FFMPEG_CAPTURE_OPTIONS", "rtsp_transport;tcp|stimeout;5000000")
import cv2  # noqa: E402
import numpy as np  # noqa: E402

WINDOW = "Camera"


def rtsp_url(ip, user, password, subtype):
    return (f"rtsp://{quote(user, safe='')}:{quote(password, safe='')}@{ip}:554"
            f"/cam/realmonitor?channel=1&subtype={subtype}")


def save_frame(frame, ip):
    name = f"snap_{ip.replace('.', '_')}_{datetime.now():%Y%m%d_%H%M%S}.jpg"
    cv2.imwrite(name, frame)
    print("📸 บันทึกภาพ:", os.path.abspath(name))


def handle_key(frame, ip):
    """คืน True ถ้าผู้ใช้สั่งออก"""
    key = cv2.waitKey(1) & 0xFF
    if key in (ord("q"), 27):
        return True
    if key == ord("s") and frame is not None:
        save_frame(frame, ip)
    # ปิดหน้าต่างด้วยปุ่ม X
    return cv2.getWindowProperty(WINDOW, cv2.WND_PROP_VISIBLE) < 1


def run_rtsp(ip, user, password, subtype):
    url = rtsp_url(ip, user, password, subtype)
    print("กำลังเชื่อมต่อ:", url.replace(quote(password, safe=""), "****"))
    cap = cv2.VideoCapture(url, cv2.CAP_FFMPEG)
    if not cap.isOpened():
        return False

    cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)  # ลดดีเลย์
    cv2.namedWindow(WINDOW, cv2.WINDOW_NORMAL)
    print("✅ เปิดสตรีมสำเร็จ  (q = ออก, s = บันทึกภาพ)")

    frames, t0, fps, fails = 0, time.time(), 0.0, 0
    while True:
        ok, frame = cap.read()
        if not ok or frame is None:
            fails += 1
            if fails > 30:
                print("⚠️ สัญญาณหลุด กำลังเชื่อมต่อใหม่...")
                cap.release()
                time.sleep(2)
                cap = cv2.VideoCapture(url, cv2.CAP_FFMPEG)
                fails = 0
            if handle_key(None, ip):
                break
            continue
        fails = 0

        frames += 1
        if time.time() - t0 >= 1:
            fps, frames, t0 = frames / (time.time() - t0), 0, time.time()
        h, w = frame.shape[:2]
        shown = frame.copy()
        cv2.putText(shown, f"{ip}  {w}x{h}  {fps:.1f} fps", (10, 30),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 0), 2)
        cv2.imshow(WINDOW, shown)
        if handle_key(frame, ip):
            break

    cap.release()
    cv2.destroyAllWindows()
    return True


def run_http(ip, user, password):
    url = f"http://{ip}/cgi-bin/snapshot.cgi?channel=1"
    mgr = urllib.request.HTTPPasswordMgrWithDefaultRealm()
    mgr.add_password(None, url, user, password)
    opener = urllib.request.build_opener(
        urllib.request.HTTPDigestAuthHandler(mgr), urllib.request.HTTPBasicAuthHandler(mgr))

    cv2.namedWindow(WINDOW, cv2.WINDOW_NORMAL)
    print("▶️ โหมดภาพนิ่ง HTTP (ช้ากว่า RTSP)  (q = ออก, s = บันทึกภาพ)")
    frame = None
    while True:
        try:
            with opener.open(url, timeout=8) as r:
                data = r.read()
            img = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
            if img is not None:
                frame = img
                cv2.imshow(WINDOW, frame)
        except Exception as e:
            print("❌ ดึงภาพไม่ได้:", e)
            time.sleep(1)
        if handle_key(frame, ip):
            break
    cv2.destroyAllWindows()


def main():
    ip = sys.argv[1] if len(sys.argv) > 1 else "192.168.1.108"
    user = sys.argv[2] if len(sys.argv) > 2 else "admin"
    subtype = sys.argv[3] if len(sys.argv) > 3 else "0"
    password = os.environ.get("CAM_PASS") or getpass.getpass(f"รหัสผ่านของ {user}@{ip}: ")

    if not run_rtsp(ip, user, password, subtype):
        print("❌ เปิด RTSP ไม่ได้ — สลับไปใช้ภาพนิ่งผ่าน HTTP")
        run_http(ip, user, password)
    return 0


if __name__ == "__main__":
    sys.exit(main())
