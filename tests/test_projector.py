"""ทดสอบโปรเจกเตอร์กับเครื่องจำลอง (PJLink + Panasonic NTCONTROL) และ API ห้อง/โปรเจกเตอร์"""
import time

import pytest

import db
import projector as pj
from conftest import closed_port
from fake_projector import FakeProjector


@pytest.fixture
def fake():
    f = FakeProjector()
    yield f
    f.close()


def wait_for(fn, timeout=8):
    end = time.time() + timeout
    while time.time() < end:
        if fn():
            return True
        time.sleep(0.1)
    return False


def test_pjlink_query_with_auth(fake):
    r = pj.pjlink_query("127.0.0.1", fake.pjlink_port, "panasonic", ["%1POWR ?", "%1INF2 ?", "%1LAMP ?"])
    assert r == {"%1POWR": "0", "%1INF2": "PT-VX420", "%1LAMP": "149 0"}


def test_pjlink_wrong_password(fake):
    with pytest.raises(pj.ProjectorAuthError):
        pj.pjlink_query("127.0.0.1", fake.pjlink_port, "wrong", ["%1POWR ?"])


def test_ntcontrol_auth_and_wrong_password(fake):
    assert pj.ntcontrol("127.0.0.1", fake.nt_port, "admin", "panasonic", "QPW") == "000"
    with pytest.raises(pj.ProjectorAuthError):
        pj.ntcontrol("127.0.0.1", fake.nt_port, "admin", "nope", "QPW")
    with pytest.raises(pj.ProjectorError):
        pj.ntcontrol("127.0.0.1", fake.nt_port, "admin", "panasonic", "XYZ")


def test_ntcontrol_er401_is_error(fake):
    """Panasonic ตอบ ER401 ตอนเครื่องปิด/วอร์ม → ต้องเป็น error ไม่ใช่ค่าที่แสดงบนหน้าเว็บ"""
    fake.pm = "ER401"
    with pytest.raises(pj.ProjectorError):
        pj.ntcontrol("127.0.0.1", fake.nt_port, "admin", "panasonic", "QPM")


def test_parse_temp():
    assert pj._parse_temp("028") == 28
    assert pj._parse_temp("0280") == 28.0
    assert pj._parse_temp("+36.5") == 36.5
    assert pj._parse_temp("") is None


def test_projector_status_and_control(fake):
    p = pj.Projector("NO17", "127.0.0.1", "admin", "panasonic", fake.pjlink_port, fake.nt_port)
    try:
        assert wait_for(lambda: p.status == "standby")
        d = p.to_dict()
        assert d["model"] == "PT-VX420" and d["serial"] == "DC7510219" and d["lamp_hours"] == 149
        assert d["temps"] == {"intake": 28, "optics": 36, "exhaust": 42}
        assert d["picture_mode"] is None and d["input"] is None  # ปิดเครื่องอยู่ ถาม input / picture mode ไม่ได้

        p.control("power", "on")
        assert wait_for(lambda: p.status == "on")
        assert wait_for(lambda: p.picture_mode == "STANDARD")  # เปิดเครื่องแล้วถามข้อมูลเสริมใหม่ทันที
        p.control("input", "COMPUTER1")
        p.control("av_mute", "on")
        p.control("picture", "CINEMA")
        p.control("volume", "up")
        assert wait_for(lambda: p.input == "COMPUTER1" and p.av_mute is True)
        assert fake.input == "11" and fake.mute == "31" and fake.pm == "CIN" and fake.volume == 11
        with pytest.raises(pj.ProjectorError):
            p.control("input", "BLURAY")
        p.control("power", "off")
        assert fake.power == "0"
    finally:
        p.stop()


def test_projector_offline():
    p = pj.Projector("x", "127.0.0.1", "admin", "", closed_port(), closed_port())
    try:
        assert wait_for(lambda: p.status == "offline")
    finally:
        p.stop()


def test_rooms_and_projector_api(client, fake):
    r = client.post("/api/rooms", json={"building": "B1 อาคารเรียนรวม 1", "name": "B1121"})
    assert r.status_code == 201
    room = r.get_json()
    assert client.post("/api/rooms", json={"name": " "}).status_code == 400

    base = {"name": "NO17", "ip": "127.0.0.1", "user": "admin", "pjlink_port": fake.pjlink_port,
            "control_port": fake.nt_port, "room_id": room["id"]}
    r = client.post("/api/projectors", json={**base, "password": "wrong"})
    assert r.status_code == 400 and "password" in r.get_json()["error"]
    r = client.post("/api/projectors", json={**base, "password": "panasonic"})
    assert r.status_code == 201, r.get_json()
    pid = r.get_json()["id"]
    assert client.post("/api/projectors", json={**base, "password": "panasonic"}).status_code == 400  # IP ซ้ำ

    # บันทึกลง DB แล้ว (รหัสผ่านเข้ารหัส)
    row = next(x for x in db.load_projectors() if x["id"] == pid)
    assert row["password"] == "panasonic" and row["room_id"] == room["id"]

    r = client.post(f"/api/projectors/{pid}/control", json={"action": "power", "value": "on"})
    assert r.status_code == 200 and fake.power == "1"
    r = client.post(f"/api/projectors/{pid}/control", json={"action": "nope"})
    assert r.status_code == 400

    # แก้ไขโดยเว้นรหัสผ่าน = ใช้รหัสเดิม
    r = client.put(f"/api/projectors/{pid}", json={**base, "name": "NO17-แก้"})
    assert r.status_code == 200 and r.get_json()["name"] == "NO17-แก้"

    # ลบห้อง → โปรเจกเตอร์ยังอยู่ แต่ไม่มีห้อง
    assert client.delete(f"/api/rooms/{room['id']}").status_code == 200
    p = next(x for x in client.get("/api/projectors").get_json() if x["id"] == pid)
    assert p["room_id"] is None
    assert client.delete(f"/api/projectors/{pid}").status_code == 200
    assert all(x["id"] != pid for x in db.load_projectors())


def test_rename_room_and_building(client):
    a = client.post("/api/rooms", json={"building": "B1", "name": "B1121"}).get_json()
    client.post("/api/rooms", json={"building": "B1", "name": "B1118"})
    client.post("/api/rooms", json={"building": "B2", "name": "B2101"})

    r = client.put(f"/api/rooms/{a['id']}", json={"building": "B1", "name": "B1121-ใหม่", "note": "ชั้น 2"})
    assert r.status_code == 200 and r.get_json()["name"] == "B1121-ใหม่"

    r = client.post("/api/buildings/rename", json={"old": "B1", "new": "B1 อาคารเรียนรวม 1"})
    assert r.status_code == 200 and r.get_json()["rooms"] == 2
    got = {x["name"]: x["building"] for x in client.get("/api/rooms").get_json()}
    assert got == {"B1121-ใหม่": "B1 อาคารเรียนรวม 1", "B1118": "B1 อาคารเรียนรวม 1", "B2101": "B2"}
    assert client.post("/api/buildings/rename", json={"old": "ไม่มี", "new": "x"}).status_code == 404
    assert client.post("/api/buildings/rename", json={"old": "B2", "new": " "}).status_code == 400


def test_room_floor_and_move_device(client, fake):
    room = client.post("/api/rooms", json={"building": "ST", "floor": "2", "name": "ST203"}).get_json()
    assert room["floor"] == "2"
    assert next(r for r in db.list_rooms() if r["id"] == room["id"])["floor"] == "2"
    p = client.post("/api/projectors", json={"name": "P1", "ip": "127.0.0.1", "user": "admin", "password": "panasonic",
                                             "pjlink_port": fake.pjlink_port, "control_port": fake.nt_port}).get_json()
    assert p["room_id"] is None

    r = client.post(f"/api/projectors/{p['id']}/room", json={"room_id": room["id"]})
    assert r.status_code == 200 and r.get_json()["room_id"] == room["id"]
    assert next(x for x in db.load_projectors() if x["id"] == p["id"])["room_id"] == room["id"]
    assert next(x for x in db.load_projectors() if x["id"] == p["id"])["password"] == "panasonic"  # รหัสเดิมไม่หาย

    assert client.post(f"/api/projectors/{p['id']}/room", json={"room_id": "nope"}).status_code == 400
    assert client.post("/api/cameras/nope/room", json={"room_id": room["id"]}).status_code == 404
    assert client.post(f"/api/others/{p['id']}/room", json={}).status_code == 404
    r = client.post(f"/api/projectors/{p['id']}/room", json={"room_id": ""})
    assert r.get_json()["room_id"] is None


def test_buildings_folders_and_order(client, fake):
    # สร้างอาคารเปล่า (โฟลเดอร์) ได้ก่อนมีห้อง
    assert client.post("/api/buildings", json={"name": "ST อาคาร 6"}).status_code == 201
    assert client.post("/api/buildings", json={"name": "ST อาคาร 6"}).status_code == 400  # ซ้ำ
    assert client.post("/api/buildings", json={"name": " "}).status_code == 400
    assert "ST อาคาร 6" in [b["name"] for b in client.get("/api/buildings").get_json()]

    # ห้องที่ใส่อาคารใหม่ → สร้างโฟลเดอร์ให้อัตโนมัติ
    a = client.post("/api/rooms", json={"building": "F อาคาร 1", "name": "F11"}).get_json()
    b = client.post("/api/rooms", json={"building": "F อาคาร 1", "name": "F12"}).get_json()
    assert "F อาคาร 1" in [x["name"] for x in client.get("/api/buildings").get_json()]

    # ลบอาคารที่ยังมีห้องไม่ได้ / อาคารว่างลบได้
    assert client.post("/api/buildings/delete", json={"name": "F อาคาร 1"}).status_code == 400
    assert client.post("/api/buildings/delete", json={"name": "ST อาคาร 6"}).status_code == 200

    # จัดลำดับห้อง / อาคาร / อุปกรณ์
    assert client.post("/api/order", json={"kind": "rooms", "ids": [b["id"], a["id"]]}).status_code == 200
    order = {r["name"]: r["sort_order"] for r in client.get("/api/rooms").get_json()}
    assert order["F12"] == 1 and order["F11"] == 2
    client.post("/api/buildings", json={"name": "A"})
    client.post("/api/order", json={"kind": "buildings", "ids": ["F อาคาร 1", "A"]})
    bo = {x["name"]: x["sort_order"] for x in client.get("/api/buildings").get_json()}
    assert bo["F อาคาร 1"] == 1 and bo["A"] == 2
    p = client.post("/api/projectors", json={"name": "P", "ip": "127.0.0.1", "user": "admin", "password": "panasonic",
                                             "pjlink_port": fake.pjlink_port, "control_port": fake.nt_port}).get_json()
    client.post("/api/order", json={"kind": "projectors", "ids": [p["id"]]})
    assert next(x for x in client.get("/api/projectors").get_json() if x["id"] == p["id"])["sort_order"] == 1
    assert next(x for x in db.load_projectors() if x["id"] == p["id"])["sort_order"] == 1
    assert client.post("/api/order", json={"kind": "users", "ids": []}).status_code == 400
    # ลำดับรวม กล้อง/โปรเจกเตอร์ สลับกันได้
    c = client.post("/api/cameras", json={"name": "C", "brand": "other", "rtsp_url": "rtsp://127.0.0.1:1/x"}).get_json()
    assert client.post("/api/order", json={"kind": "devices",
                                           "ids": [f"camera:{c['id']}", f"projector:{p['id']}"]}).status_code == 200
    assert next(x for x in client.get("/api/cameras").get_json() if x["id"] == c["id"])["sort_order"] == 1
    assert next(x for x in client.get("/api/projectors").get_json() if x["id"] == p["id"])["sort_order"] == 2
    assert client.post("/api/order", json={"kind": "devices", "ids": ["printer:x"]}).status_code == 400

    # เปลี่ยนชื่ออาคาร → ห้องและโฟลเดอร์เปลี่ยนตาม
    client.post("/api/buildings/rename", json={"old": "F อาคาร 1", "new": "F อาคารเรียนรวม 1"})
    assert {r["building"] for r in client.get("/api/rooms").get_json()} == {"F อาคารเรียนรวม 1"}
    names = [x["name"] for x in client.get("/api/buildings").get_json()]
    assert "F อาคารเรียนรวม 1" in names and "F อาคาร 1" not in names
    for n in names:
        db.delete_building(n) if n == "A" else None


def test_camera_room_must_exist(client):
    r = client.post("/api/cameras", json={"brand": "other", "rtsp_url": "rtsp://127.0.0.1:1/x",
                                          "room_id": "nosuchroom"})
    assert r.status_code == 400 and "ห้อง" in r.get_json()["error"]
