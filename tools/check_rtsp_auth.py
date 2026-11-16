"""ตรวจว่ากล้องเปิดดูภาพ RTSP ได้โดยไม่ใส่รหัสหรือไม่ (ไม่ใช้รหัสใด ๆ — แค่ถามกล้องแบบไม่ล็อกอิน)

  python tools/check_rtsp_auth.py                      # กล้อง Compro ชุดห้อง F9 / vitsavapat
  python tools/check_rtsp_auth.py 192.168.11.110 ...   # ระบุ IP เอง
  python tools/check_rtsp_auth.py --path /medias1 IP   # path อื่น

ผล:  เปิดอยู่ (ไม่ต้องใช้รหัส)  = DESCRIBE ได้ 200   -> ใครอยู่ในวงเครือข่ายก็ดูภาพได้
     ล็อกแล้ว (ต้องใช้รหัส)     = DESCRIBE ได้ 401   -> ต้องมี user/pass
"""
import argparse
import socket

DEFAULT = ["192.168.11.16", "192.168.11.108", "192.168.11.110", "192.168.11.112", "192.168.11.114",
           "192.168.11.116"]


def describe(ip, port, path, timeout=4):
    with socket.create_connection((ip, port), timeout=timeout) as s:
        s.settimeout(timeout)
        s.sendall(f"DESCRIBE rtsp://{ip}:{port}{path} RTSP/1.0\r\nCSeq: 1\r\nAccept: application/sdp\r\n"
                  f"User-Agent: check_rtsp_auth\r\n\r\n".encode())
        data = s.recv(2048).decode(errors="replace")
    lines = data.split("\r\n")
    auth = next((l.split(":", 1)[1].strip() for l in lines if l.lower().startswith("www-authenticate")), "")
    return (lines[0] if lines else ""), auth


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("ips", nargs="*", default=DEFAULT)
    ap.add_argument("--port", type=int, default=554)
    ap.add_argument("--path", default="/medias1")
    a = ap.parse_args()
    open_n = 0
    for ip in a.ips:
        try:
            status, auth = describe(ip, a.port, a.path)
        except OSError as e:
            print(f"{ip:16} ติดต่อไม่ได้ ({e.__class__.__name__})")
            continue
        code = status.split(" ")[1] if " " in status else "?"
        if code == "200":
            open_n += 1
            print(f"{ip:16} เปิดอยู่ — ดูภาพได้โดยไม่ใส่รหัส  ({status})")
        elif code == "401":
            kind = auth.split(" ")[0] if auth else "?"
            print(f"{ip:16} ล็อกแล้ว — ต้องใช้รหัส ({kind})")
        else:
            print(f"{ip:16} {status}")
    print(f"\nสรุป: เปิดโดยไม่ใช้รหัส {open_n} ตัว")


if __name__ == "__main__":
    main()
