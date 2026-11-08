"""ทดสอบดึงภาพจากกล้อง Dahua ผ่าน RTSP แล้วบันทึกเป็นรูป snapshot

วิธีใช้:
    pip install opencv-python
    python tools/test_camera.py 192.168.1.108 admin

สคริปต์จะถามรหัสผ่าน (พิมพ์แล้วไม่แสดงบนจอ) จึงไม่ต้องเขียนรหัสลงในไฟล์หรือคำสั่ง
"""
import os
import sys
import getpass
from urllib.parse import quote

os.environ.setdefault("OPENCV_FFMPEG_CAPTURE_OPTIONS", "rtsp_transport;tcp")
import cv2  # noqa: E402


def http_snapshot(ip, user, password):
    """ดึงภาพนิ่งจาก Dahua ผ่าน /cgi-bin/snapshot.cgi (ใช้ Digest Auth บนพอร์ต 80)"""
    import urllib.request
    url = f"http://{ip}/cgi-bin/snapshot.cgi?channel=1"
    mgr = urllib.request.HTTPPasswordMgrWithDefaultRealm()
    mgr.add_password(None, url, user, password)
    opener = urllib.request.build_opener(
        urllib.request.HTTPDigestAuthHandler(mgr), urllib.request.HTTPBasicAuthHandler(mgr))
    try:
        with opener.open(url, timeout=8) as r:
            data = r.read()
            ctype = r.headers.get("Content-Type", "")
    except Exception as e:
        print("❌ ดึงภาพนิ่งไม่ได้:", e)
        return 2
    if not data.startswith(b"\xff\xd8"):
        print("❌ กล้องตอบกลับแต่ไม่ใช่รูป JPEG:", ctype, data[:120])
        return 3
    out = f"snapshot_{ip.replace('.', '_')}.jpg"
    with open(out, "wb") as fh:
        fh.write(data)
    print(f"✅ ได้ภาพนิ่งผ่าน HTTP แล้ว บันทึกที่ {os.path.abspath(out)}")
    print("   (RTSP ยังใช้ไม่ได้ ต้องแก้ที่การตั้งค่ากล้องก่อนจึงจะดูภาพสดได้)")
    return 0


def main():
    if len(sys.argv) < 3:
        print("ใช้: python tools/test_camera.py <IP> <USER> [subtype 0|1]")
        return 1
    ip, user = sys.argv[1], sys.argv[2]
    subtype = sys.argv[3] if len(sys.argv) > 3 else "1"
    password = getpass.getpass(f"รหัสผ่านของ {user}@{ip}: ")

    url = (f"rtsp://{quote(user, safe='')}:{quote(password, safe='')}@{ip}:554"
           f"/cam/realmonitor?channel=1&subtype={subtype}")
    shown = url.replace(quote(password, safe=""), "****")
    print("กำลังเชื่อมต่อ:", shown)

    cap = cv2.VideoCapture(url, cv2.CAP_FFMPEG)
    if not cap.isOpened():
        print("❌ เปิดสตรีม RTSP ไม่ได้ — ลองดึงภาพนิ่งผ่าน HTTP (พอร์ต 80) แทน...")
        return http_snapshot(ip, user, password)
    ok, frame = cap.read()
    cap.release()
    if not ok or frame is None:
        print("❌ เชื่อมต่อได้แต่ไม่ได้ภาพ — ลอง subtype อื่น เช่น: python tools/test_camera.py", ip, user, "0")
        return 3

    out = f"snapshot_{ip.replace('.', '_')}.jpg"
    cv2.imwrite(out, frame)
    h, w = frame.shape[:2]
    print(f"✅ ได้ภาพแล้ว ขนาด {w}x{h} บันทึกที่ {os.path.abspath(out)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
