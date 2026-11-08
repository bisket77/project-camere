// โหมดเดโมสำหรับ GitHub Pages: ไม่มี backend จริง ไม่มีกล้องจริง
// ดัก fetch("/api/...") และรูป <img src="/api/..."> แล้วตอบด้วยข้อมูลจำลองในเบราว์เซอร์
// ภาพกล้องเป็น SVG เคลื่อนไหวที่สร้างขึ้นเอง (ไม่มี IP / รหัสผ่าน / ภาพจริงใด ๆ)
(() => {
  const now = () => Date.now() / 1000;
  const STATUS_TEXT = {
    connecting: "กำลังเชื่อมต่อ", online: "ออนไลน์", snapshot: "ออนไลน์ (ภาพนิ่ง)", mjpeg: "ออนไลน์ (MJPEG)",
    offline: "ออฟไลน์", auth_error: "รหัสผ่านผิด", stream_error: "ดึงภาพไม่ได้",
  };
  let nextId = 1;
  const cams = [];

  function makeCam(d, status = "online") {
    const ip = d.ip || "192.0.2." + (100 + nextId);  // 192.0.2.x = IP สำหรับตัวอย่างเอกสาร ไม่มีเครื่องจริง
    const cam = {
      id: "demo" + nextId++, name: d.name || "กล้อง " + ip, brand: d.brand || "dahua", ip,
      quality: d.quality || "sub", user: d.user || "admin", room_id: d.room_id || null,
      http_port: +d.http_port || 80, rtsp_port: +d.rtsp_port || 554, rtsp_url: d.rtsp_url || "",
      model: "DEMO-CAM-1230", serial: "DEMO" + String(nextId).padStart(6, "0"), firmware: "demo 1.0",
      device_name: "Demo", viewers: 0, last_online_at: now() - 600,
    };
    setStatus(cam, status);
    return cam;
  }
  function setStatus(cam, status) {
    const on = ["online", "snapshot", "mjpeg"].includes(status);
    Object.assign(cam, {
      status, status_text: STATUS_TEXT[status], mode: on ? "mjpeg" : "",
      last_error: status === "offline" ? "ติดต่อกล้องไม่ได้ (เดโม)" : status === "auth_error" ? "ชื่อผู้ใช้หรือรหัสผ่านไม่ถูกต้อง (เดโม)" : "",
      has_frame: on, resolution: on ? "704x480" : "", fps: on ? 25 : 0,
      online_since: on ? now() - Math.floor(Math.random() * 90000) : null,
      latency_ms: status === "offline" ? null : 1 + Math.floor(Math.random() * 4),
    });
    cam.rtsp = `rtsp://${cam.ip}:${cam.rtsp_port}/cam/realmonitor?channel=1&subtype=1`;
  }
  // ห้อง / อาคาร ตัวอย่าง
  let extraBuildings = ["F อาคารเรียนรวม 1"];  // อาคารที่สร้างไว้แต่ยังไม่มีห้อง
  const order = {};  // ลำดับอาคารที่จัดเอง
  const rooms = [
    {id: "r1", building: "ST อาคารเรียนรวม 6", floor: "2", name: "ST203", note: "ห้องบรรยายใหญ่"},
    {id: "r2", building: "ST อาคารเรียนรวม 6", floor: "2", name: "ST204", note: ""},
    {id: "r4", building: "ST อาคารเรียนรวม 6", floor: "3", name: "ST301", note: ""},
    {id: "r3", building: "B1 อาคารเรียนรวม 1", floor: "1", name: "B1121", note: ""},
  ];
  cams.push(
    makeCam({name: "หน้าห้อง", ip: "192.0.2.11", room_id: "r1"}),
    makeCam({name: "หลังห้อง", ip: "192.0.2.12", room_id: "r1"}),
    makeCam({name: "หน้าห้อง", ip: "192.0.2.21", room_id: "r2"}),
    makeCam({name: "โถงกลาง", ip: "192.0.2.31", room_id: "r3"}, "offline"),
    makeCam({name: "ห้องประชุม", ip: "192.0.2.41", brand: "hikvision"}, "auth_error"),
  );

  // โปรเจกเตอร์จำลอง
  const PSTATUS = {on: "เปิดอยู่", standby: "Standby", warming: "กำลังวอร์มเครื่อง", cooling: "กำลังระบายความร้อน",
                   offline: "ติดต่อไม่ได้", error: "เครื่องแจ้งเตือน"};
  let nextP = 1;
  function makeProj(d, power = "standby") {
    const p = {id: "p" + nextP++, name: d.name || "โปรเจกเตอร์", ip: d.ip || "192.0.2." + (200 + nextP),
      user: d.user || "admin1", room_id: d.room_id || null, pjlink_port: +d.pjlink_port || 4352,
      control_port: +d.control_port || 1024, power, input: "HDMI", av_mute: false, picture_mode: "STANDARD",
      lamp_hours: 100 + Math.floor(Math.random() * 900), model: "PT-VX420", serial: "DEMO" + (7510000 + nextP),
      projector_name: d.name || "", firmware: "1.04", manufacturer: "Panasonic", has_pjlink: true, has_ntcontrol: true,
      errors: {"พัดลม": "ok", "หลอดไฟ": "ok", "อุณหภูมิ": "ok", "ฝาครอบ": "ok", "ฟิลเตอร์": "ok", "อื่น ๆ": "ok"},
      temps: {intake: 28, optics: 36, exhaust: 42}, latency_ms: 4, last_error: ""};
    setPStatus(p);
    return p;
  }
  function setPStatus(p) {
    p.status = p.offline ? "offline" : p.power;
    p.status_text = PSTATUS[p.status];
    p.last_error = p.offline ? "ติดต่อ " + p.ip + " ไม่ได้ (เดโม)" : "";
    if (p.offline) Object.assign(p, {power: null, input: null, latency_ms: null});
  }
  const projs = [
    makeProj({name: "NO17", ip: "192.0.2.101", room_id: "r1"}, "on"),
    makeProj({name: "NO18", ip: "192.0.2.102", room_id: "r2"}),
    makeProj({name: "NO21", ip: "192.0.2.103", room_id: "r3"}),
  ];
  projs[2].offline = true; setPStatus(projs[2]);
  function projControl(p, action, value) {
    if (p.offline) return "ติดต่อโปรเจกเตอร์ไม่ได้ (เดโม)";
    if (action === "power") {
      p.power = value === "on" ? "warming" : "cooling"; setPStatus(p);
      setTimeout(() => { p.power = value === "on" ? "on" : "standby"; setPStatus(p); }, 3000);
    } else if (p.power !== "on") {
      return "ทำไม่ได้ในสถานะนี้ (เช่น เครื่องปิดอยู่)";
    } else if (action === "input") p.input = value;
    else if (action === "av_mute") p.av_mute = value === "on";
    else if (action === "picture") p.picture_mode = value;
    return "";
  }
  // ค่า ping ขยับเล็กน้อยให้ดูเหมือนสด
  setInterval(() => cams.forEach(c => { if (c.latency_ms != null) c.latency_ms = 1 + Math.floor(Math.random() * 4); }), 2000);

  const hue = id => [...id].reduce((h, ch) => h + ch.charCodeAt(0) * 37, 0) % 360;
  // ภาพกล้องจำลอง: SVG มีแถบเคลื่อนที่ + นาฬิกา (SMIL animation ทำงานใน <img> ได้)
  function frameSvg(cam, w) {
    const h = Math.round(w * 9 / 16), H = hue(cam.id), t = new Date().toLocaleTimeString("th-TH");
    const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="${w}" height="${h}" viewBox="0 0 640 360">
      <defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1">
        <stop offset="0" stop-color="hsl(${H},45%,22%)"/><stop offset="1" stop-color="hsl(${(H + 60) % 360},45%,12%)"/></linearGradient></defs>
      <rect width="640" height="360" fill="url(#g)"/>
      <g stroke="rgba(255,255,255,.08)">${Array.from({length: 15}, (_, i) => `<line x1="${i * 46}" y1="0" x2="${i * 46}" y2="360"/>`).join("")}</g>
      <rect y="250" width="120" height="50" rx="8" fill="hsl(${(H + 180) % 360},60%,55%)">
        <animate attributeName="x" values="-120;640" dur="4s" repeatCount="indefinite"/></rect>
      <circle cx="320" cy="150" r="40" fill="none" stroke="rgba(255,255,255,.5)" stroke-width="4">
        <animate attributeName="r" values="30;60;30" dur="2s" repeatCount="indefinite"/></circle>
      <text x="16" y="34" fill="#fff" font-family="monospace" font-size="22">${cam.name.replace(/[<&>]/g, "")}</text>
      <text x="16" y="344" fill="#fff" font-family="monospace" font-size="18">DEMO · ${cam.ip} · ${t}</text>
      <circle cx="616" cy="26" r="8" fill="#e33"><animate attributeName="opacity" values="1;0;1" dur="1s" repeatCount="indefinite"/></circle>
    </svg>`;
    return "data:image/svg+xml;charset=utf-8," + encodeURIComponent(svg);
  }

  // ---------- รูปภาพ: เปลี่ยน /api/cameras/<id>/snapshot.jpg | stream.mjpg เป็น SVG ----------
  const desc = Object.getOwnPropertyDescriptor(HTMLImageElement.prototype, "src");
  Object.defineProperty(HTMLImageElement.prototype, "src", {
    get() { return desc.get.call(this); },
    set(v) {
      const m = /\/api\/cameras\/([^/]+)\/(snapshot\.jpg|stream\.mjpg)(?:\?w=(\d+))?/.exec(String(v));
      if (m) {
        const cam = cams.find(c => c.id === m[1]);
        v = cam ? frameSvg(cam, +(m[3] || 1280)) : "";
      }
      desc.set.call(this, v);
    },
  });

  // ---------- API ----------
  const json = (data, status = 200) => new Response(JSON.stringify(data), {status, headers: {"Content-Type": "application/json"}});
  const realFetch = window.fetch.bind(window);
  window.fetch = async (input, opts = {}) => {
    const url = new URL(typeof input === "string" ? input : input.url, location.href);
    const path = url.pathname.replace(/^.*?(\/api\/)/, "/api/");
    if (!path.startsWith("/api/")) return realFetch(input, opts);
    const method = (opts.method || "GET").toUpperCase();
    const body = opts.body ? JSON.parse(opts.body) : {};
    await new Promise(r => setTimeout(r, 150));
    let m;
    if (path === "/api/cameras" && method === "GET") return json(cams);
    if (path === "/api/cameras" && method === "POST") {
      if (!body.ip && body.brand !== "other") return json({error: "กรุณากรอก IP"}, 400);
      if (cams.some(c => c.ip === body.ip && body.brand !== "other")) return json({error: "IP นี้ถูกเพิ่มไปแล้ว"}, 400);
      const cam = makeCam(body); cams.push(cam); return json(cam, 201);
    }
    if (path === "/api/cameras/reconnect") { cams.forEach(c => setStatus(c, c.status === "offline" ? "offline" : c.status)); return json({ok: true}); }
    if ((m = /^\/api\/cameras\/([^/]+)$/.exec(path))) {
      const i = cams.findIndex(c => c.id === m[1]);
      if (i < 0) return json({error: "ไม่พบกล้อง"}, 404);
      if (method === "DELETE") { cams.splice(i, 1); return json({ok: true}); }
      if (method === "PUT") {
        const c = cams[i];
        for (const k of ["name", "brand", "ip", "quality", "user", "rtsp_url"]) if (body[k] !== undefined) c[k] = body[k];
        c.room_id = body.room_id || null;
        c.http_port = +body.http_port || c.http_port; c.rtsp_port = +body.rtsp_port || c.rtsp_port;
        setStatus(c, "online");
        return json(c);
      }
    }
    if ((m = /^\/api\/cameras\/([^/]+)\/reconnect$/.exec(path))) {
      const c = cams.find(x => x.id === m[1]);
      if (c) setStatus(c, "online");
      return json({ok: true});
    }
    // ----- ห้อง -----
    if (path === "/api/rooms" && method === "GET") return json(rooms);
    if (path === "/api/rooms" && method === "POST") {
      if (!(body.name || "").trim()) return json({error: "กรุณาใส่ชื่อห้อง เช่น B1121"}, 400);
      const r = {id: "r" + Date.now(), building: body.building || "", floor: body.floor || "", name: body.name.trim(), note: body.note || ""};
      rooms.push(r); return json(r, 201);
    }
    if ((m = /^\/api\/rooms\/([^/]+)$/.exec(path))) {
      const i = rooms.findIndex(r => r.id === m[1]);
      if (i < 0) return json({error: "ไม่พบห้อง"}, 404);
      if (method === "DELETE") {
        rooms.splice(i, 1);
        [...cams, ...projs].forEach(x => { if (x.room_id === m[1]) x.room_id = null; });
        return json({ok: true});
      }
      Object.assign(rooms[i], {building: body.building || "", floor: body.floor || "", name: (body.name || rooms[i].name).trim(), note: body.note || ""});
      return json(rooms[i]);
    }
    if ((m = /^\/api\/(cameras|projectors)\/([^/]+)\/room$/.exec(path))) {
      const dev = (m[1] === "cameras" ? cams : projs).find(x => x.id === m[2]);
      if (!dev) return json({error: "ไม่พบอุปกรณ์"}, 404);
      if (body.room_id && !rooms.some(r => r.id === body.room_id)) return json({error: "ไม่พบห้องที่เลือก"}, 400);
      dev.room_id = body.room_id || null;
      return json(dev);
    }
    if (path === "/api/buildings" && method === "GET") {
      const names = [...new Set([...extraBuildings, ...rooms.map(r => r.building).filter(Boolean)])];
      return json(names.map(n => ({name: n, sort_order: order[n] || 0})));
    }
    if (path === "/api/buildings" && method === "POST") {
      const n = (body.name || "").trim();
      if (!n) return json({error: "กรุณาใส่ชื่ออาคาร"}, 400);
      if (extraBuildings.includes(n) || rooms.some(r => r.building === n)) return json({error: `มีอาคาร ${n} อยู่แล้ว`}, 400);
      extraBuildings.push(n); return json({name: n}, 201);
    }
    if (path === "/api/buildings/delete") {
      if (rooms.some(r => r.building === body.name)) return json({error: "ยังมีห้องอยู่ในอาคารนี้ ย้ายหรือลบห้องก่อน"}, 400);
      extraBuildings = extraBuildings.filter(n => n !== body.name); return json({ok: true});
    }
    if (path === "/api/order") {
      (body.ids || []).forEach((id, i) => {
        if (body.kind === "buildings") order[id] = i + 1;
        if (body.kind === "devices") {
          const [k, did] = id.split(":");
          const dev = (k === "camera" ? cams : projs).find(o => o.id === did);
          if (dev) dev.sort_order = i + 1;
          return;
        }
        const list = {rooms, cameras: cams, projectors: projs}[body.kind];
        const x = list && list.find(o => o.id === id);
        if (x) x.sort_order = i + 1;
      });
      return json({ok: true});
    }
    if (path === "/api/buildings/rename") {
      if (extraBuildings.includes(body.old)) extraBuildings = extraBuildings.map(n => (n === body.old ? (body.new || "").trim() : n));
      const nw = (body.new || "").trim();
      if (!nw) return json({error: "กรุณาใส่ชื่ออาคาร"}, 400);
      const hit = rooms.filter(r => r.building === (body.old || ""));
      hit.forEach(r => { r.building = nw; });
      return json({ok: true, rooms: hit.length});
    }
    // ----- โปรเจกเตอร์ -----
    if (path === "/api/projectors" && method === "GET") return json(projs);
    if (path === "/api/projectors" && method === "POST") {
      if (!body.ip) return json({error: "IP ไม่ถูกต้อง"}, 400);
      if (projs.some(p => p.ip === body.ip)) return json({error: `มีโปรเจกเตอร์ ${body.ip} อยู่ในรายการแล้ว`}, 400);
      const p = makeProj(body); projs.push(p); return json(p, 201);
    }
    if ((m = /^\/api\/projectors\/([^/]+)(\/control|\/refresh)?$/.exec(path))) {
      const i = projs.findIndex(p => p.id === m[1]);
      if (i < 0) return json({error: "ไม่พบโปรเจกเตอร์"}, 404);
      const p = projs[i];
      if (m[2] === "/control") { const err = projControl(p, body.action, body.value); return err ? json({error: err}, 400) : json(p); }
      if (m[2] === "/refresh") return json({ok: true});
      if (method === "DELETE") { projs.splice(i, 1); return json({ok: true}); }
      if (method === "PUT") {
        Object.assign(p, {name: body.name || p.name, ip: body.ip || p.ip, user: body.user || p.user, room_id: body.room_id || null});
        return json(p);
      }
    }
    return json({error: "ไม่มีในโหมดเดโม"}, 404);
  };
})();
