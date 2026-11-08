"""ทดสอบ REST API ของเว็บ (เพิ่ม / แก้ไข / ลบ / ตรวจข้อมูล) โดยไม่ต้องมีกล้องจริง"""
import db
from conftest import closed_port


def _other(port, **kw):
    body = {"name": "กล้องทดสอบ", "brand": "other", "user": "admin", "password": "pw-123",
            "rtsp_url": f"rtsp://127.0.0.1:{port}/live"}
    body.update(kw)
    return body


def test_index_page(client):
    r = client.get("/")
    assert r.status_code == 200
    assert b"Classroom Monitor" in r.data


def test_add_list_edit_delete(client):
    port = closed_port()
    r = client.post("/api/cameras", json=_other(port))
    assert r.status_code == 201
    cam = r.get_json()
    assert cam["ip"] == "127.0.0.1"
    assert "pw-123" not in r.get_data(as_text=True)          # ห้ามส่งรหัสผ่านกลับไปหน้าเว็บ
    assert any(c["id"] == cam["id"] for c in client.get("/api/cameras").get_json())
    assert any(row["id"] == cam["id"] for row in db.load_all())  # บันทึกลงฐานข้อมูลแล้ว

    # แก้ไขโดยเว้นรหัสผ่านว่าง → ต้องใช้รหัสเดิม
    r = client.put(f"/api/cameras/{cam['id']}", json=_other(port, name="แก้ชื่อแล้ว", password=""))
    assert r.status_code == 200
    assert r.get_json()["name"] == "แก้ชื่อแล้ว"
    row = next(x for x in db.load_all() if x["id"] == cam["id"])
    assert row["password"] == "pw-123" and row["name"] == "แก้ชื่อแล้ว"

    assert client.delete(f"/api/cameras/{cam['id']}").status_code == 200
    assert all(x["id"] != cam["id"] for x in db.load_all())
    assert client.delete(f"/api/cameras/{cam['id']}").status_code == 404


def test_reconnect_endpoints(client):
    cam = client.post("/api/cameras", json=_other(closed_port())).get_json()
    assert client.post(f"/api/cameras/{cam['id']}/reconnect").status_code == 200
    assert client.post("/api/cameras/reconnect").get_json()["ok"] is True


def test_validation_errors(client):
    r = client.post("/api/cameras", json={"brand": "other", "rtsp_url": "http://not-rtsp"})
    assert r.status_code == 400
    r = client.post("/api/cameras", json={"brand": "dahua", "ip": "bad ip!", "user": "admin"})
    assert r.status_code == 400
    r = client.post("/api/cameras", json={"brand": "unknown", "ip": "127.0.0.1"})
    assert r.status_code == 400


def test_rtsps_url_accepted(client):
    """RTSP over TLS (rtsps://) ใส่เองได้ และใส่ username/password ให้อัตโนมัติ แต่ไม่ส่งรหัสกลับมาที่หน้าเว็บ"""
    port = closed_port()
    r = client.post("/api/cameras", json={"brand": "other", "user": "admin", "password": "pw-secret-1",
                                          "rtsp_url": f"RTSPS://127.0.0.1:{port}/cam/realmonitor?channel=1&subtype=0"})
    assert r.status_code == 201, r.get_json()
    cam = r.get_json()
    assert cam["rtsp_url"].startswith("rtsps://127.0.0.1") and "pw-secret-1" not in r.get_data(as_text=True)
    import app as camapp
    assert camapp.cameras[cam["id"]].custom_rtsp.startswith("rtsps://admin:pw-secret-1@127.0.0.1")
    assert camapp.cameras[cam["id"]].rtsp_tls is True


def test_unreachable_dahua_rejected(client):
    """กล้อง Dahua ที่ติดต่อไม่ได้ ต้องไม่ถูกเพิ่ม (ไม่ลองรหัสผ่านซ้ำ ๆ จนกล้องล็อก)"""
    r = client.post("/api/cameras", json={"brand": "dahua", "ip": "127.0.0.1", "user": "admin",
                                          "password": "x", "http_port": closed_port(),
                                          "rtsp_port": closed_port()})
    assert r.status_code == 400


def test_unknown_camera_404(client):
    assert client.get("/api/cameras/nope/snapshot.jpg").status_code == 404
    assert client.get("/api/cameras/nope/stream.mjpg").status_code == 404
