"""โปรเจกเตอร์จำลอง (PJLink + Panasonic NTCONTROL) ใช้ทดสอบโดยไม่ต้องมีเครื่องจริง

รันเดี่ยว ๆ ได้:  python tests/fake_projector.py  (PJLink 14352, NTCONTROL 11024, รหัส admin/panasonic)
"""
import hashlib
import socket
import threading


class FakeProjector:
    def __init__(self, user="admin", password="panasonic", pjlink_port=0, nt_port=0, host="127.0.0.1"):
        self.user, self.password = user, password
        self.power, self.input, self.mute, self.pm, self.volume = "0", "31", "30", "STD", 10
        self.lamp = 149
        self.erst = "000000"
        self.commands = []   # คำสั่งที่ได้รับ (ไว้ตรวจในเทสต์)
        self._pj = self._listen(host, pjlink_port, self._pjlink_client)
        self._nt = self._listen(host, nt_port, self._nt_client)
        self.pjlink_port, self.nt_port = self._pj.getsockname()[1], self._nt.getsockname()[1]

    def _listen(self, host, port, handler):
        s = socket.socket()
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind((host, port))
        s.listen(8)

        def loop():
            while True:
                try:
                    c, _ = s.accept()
                except OSError:
                    return
                threading.Thread(target=handler, args=(c,), daemon=True).start()
        threading.Thread(target=loop, daemon=True).start()
        return s

    def close(self):
        self._pj.close()
        self._nt.close()

    @staticmethod
    def _readline(c):
        buf = b""
        while not buf.endswith(b"\r"):
            try:
                ch = c.recv(1)
            except OSError:  # ฝั่งโปรแกรมเช็กพอร์ตแล้วตัดทันที
                return None
            if not ch:
                return None
            buf += ch
        return buf.decode().strip("\r")

    # ---------- PJLink ----------
    def _pjlink_client(self, c):
        rand = "498e4a67"
        with c:
            c.sendall(f"PJLINK 1 {rand}\r".encode())
            first = True
            while True:
                line = self._readline(c)
                if line is None:
                    return
                if first:
                    if line[:32] != hashlib.md5((rand + self.password).encode()).hexdigest():
                        c.sendall(b"PJLINK ERRA\r")
                        return
                    line, first = line[32:], False
                self.commands.append(line)
                c.sendall((self._pjlink(line) + "\r").encode())

    def _pjlink(self, line):
        cmd, _, arg = line.partition(" ")
        key = cmd[:6]
        on = self.power == "1"
        if arg == "?":
            val = {"%1POWR": self.power, "%1INPT": self.input if on else "ERR3",
                   "%1AVMT": self.mute if on else "ERR3", "%1LAMP": f"{self.lamp} {int(on)}",
                   "%1ERST": self.erst, "%1NAME": "NO17", "%1INF1": "Panasonic", "%1INF2": "PT-VX420",
                   "%1CLSS": "2", "%2SNUM": "DC7510219", "%2SVER": "1.04"}.get(key, "ERR1")
        elif key == "%1POWR":
            self.power, val = ("1" if arg == "1" else "0"), "OK"
        elif key == "%1INPT":
            if not on:
                val = "ERR3"
            else:
                self.input, val = arg, "OK"
        elif key == "%1AVMT":
            self.mute, val = arg, "OK"
        else:
            val = "ERR1"
        return f"{key}={val}"

    # ---------- NTCONTROL ----------
    def _nt_client(self, c):
        rand = "zyxwvuts"
        with c:
            c.sendall(f"NTCONTROL 1 {rand}\r".encode())
            line = self._readline(c)
            if line is None:
                return
            if line[:32] != hashlib.md5(f"{self.user}:{self.password}:{rand}".encode()).hexdigest():
                c.sendall(b"ERRA\r")
                return
            cmd = line[34:]   # ตัด hash + "00"
            self.commands.append("NT:" + cmd)
            r = self._nt_cmd(cmd)
            c.sendall(((r if r.startswith("ERR") else "00" + r) + "\r").encode())

    def _nt_cmd(self, cmd):
        if cmd == "QPW":
            return "001" if self.power == "1" else "000"
        if cmd == "PON":
            self.power = "1"
            return cmd
        if cmd == "POF":
            self.power = "0"
            return cmd
        if cmd == "QPM":
            return self.pm
        if cmd.startswith("VPM:"):
            self.pm = cmd[4:]
            return cmd
        if cmd == "AUU":
            self.volume += 1
            return cmd
        if cmd == "AUD":
            self.volume -= 1
            return cmd
        if cmd.startswith("QTM:"):
            return {"0": "028", "1": "036", "2": "042"}.get(cmd[4:], "ERR2")
        return "ERR1"


if __name__ == "__main__":
    import time
    f = FakeProjector(pjlink_port=14352, nt_port=11024)
    print(f"โปรเจกเตอร์จำลอง: PJLink {f.pjlink_port}, NTCONTROL {f.nt_port} (admin / panasonic)")
    while True:
        time.sleep(3600)
