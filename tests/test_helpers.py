"""ทดสอบฟังก์ชันช่วยที่ไม่ต้องใช้กล้องจริง"""
import datetime
import socket
import ssl
import threading

import cv2
import numpy as np
import pytest

import app as camapp
from conftest import closed_port


def test_jpeg_size_reads_header():
    img = np.zeros((360, 640, 3), np.uint8)
    data = cv2.imencode(".jpg", img)[1].tobytes()
    assert camapp.jpeg_size(data) == (640, 360)


def test_jpeg_size_rejects_garbage():
    assert camapp.jpeg_size(b"not a jpeg at all") is None


@pytest.mark.parametrize("extradata, expected", [
    (bytes([1, 0x64, 0x00, 0x1F, 0xFF]), "avc1.64001F"),                    # avcC
    (bytes([0, 0, 0, 1, 0x67, 0x4D, 0x40, 0x1E, 0x95]), "avc1.4D401E"),      # Annex B (start code 4 ไบต์)
    (bytes([0, 0, 1, 0x67, 0x42, 0xC0, 0x1F, 0x00]), "avc1.42C01F"),         # Annex B (start code 3 ไบต์)
    (b"", "avc1.640029"),                                                  # ไม่มีข้อมูล → ค่ากลาง
])
def test_avc_codec_string(extradata, expected):
    assert camapp.avc_codec_string(extradata) == expected


def test_tcp_latency_closed_port():
    assert camapp.tcp_latency("127.0.0.1", closed_port(), timeout=0.5) is None


def test_parse_kv():
    assert camapp.parse_kv("deviceType=IPC-X\nserialNumber=ABC\nbad line") == {
        "deviceType": "IPC-X", "serialNumber": "ABC"}


# ---------- RTSP ธรรมดา / RTSP over TLS ----------

def _rtsp_server(tls_ctx=None):
    """เซิร์ฟเวอร์ RTSP จำลอง ตอบ 401 แบบกล้อง Dahua (ถ้าใส่ tls_ctx จะเป็นแบบเข้ารหัส)"""
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(8)

    def serve():
        while True:
            try:
                conn, _ = srv.accept()
            except OSError:
                return
            try:
                if tls_ctx:
                    conn = tls_ctx.wrap_socket(conn, server_side=True)
                if conn.recv(1024).startswith(b"OPTIONS"):
                    conn.sendall(b"RTSP/1.0 401 Unauthorized\r\nCSeq: 1\r\n\r\n")
            except (OSError, ssl.SSLError):
                pass
            finally:
                conn.close()

    threading.Thread(target=serve, daemon=True).start()
    return srv


@pytest.fixture(scope="module")
def tls_server_ctx(tmp_path_factory):
    """สร้างใบรับรอง self-signed แบบที่กล้องใช้"""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "fake-camera")])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(now)
            .not_valid_after(now + datetime.timedelta(days=1)).sign(key, hashes.SHA256()))
    d = tmp_path_factory.mktemp("cert")
    (d / "c.pem").write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    (d / "k.pem").write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                                serialization.NoEncryption()))
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(d / "c.pem", d / "k.pem")
    return ctx


def test_detect_plain_rtsp():
    srv = _rtsp_server()
    try:
        assert camapp.detect_rtsp_tls("127.0.0.1", srv.getsockname()[1]) is False
    finally:
        srv.close()


def test_detect_tls_rtsp(tls_server_ctx):
    srv = _rtsp_server(tls_server_ctx)
    try:
        assert camapp.detect_rtsp_tls("127.0.0.1", srv.getsockname()[1]) is True
    finally:
        srv.close()


def test_detect_nothing_listening():
    assert camapp.detect_rtsp_tls("127.0.0.1", closed_port(), timeout=0.5) is None


def test_tls_proxy_relays_plain_to_tls(tls_server_ctx):
    """OpenCV/PyAV ต่อแบบธรรมดาเข้าตัวกลาง → ตัวกลางส่งต่อแบบ TLS ถึงกล้อง และส่งคำตอบกลับมา"""
    srv = _rtsp_server(tls_server_ctx)
    proxy = camapp.TlsProxy("127.0.0.1", srv.getsockname()[1])
    try:
        with socket.create_connection(("127.0.0.1", proxy.local_port), timeout=5) as s:
            s.sendall(camapp.rtsp_ping("127.0.0.1", proxy.local_port))
            assert s.recv(64).startswith(b"RTSP/1.0 401")
    finally:
        proxy.close()
        srv.close()
