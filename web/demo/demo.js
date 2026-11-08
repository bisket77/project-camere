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
      quality: d.quality || "sub", user: d.user || "admin",
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
  cams.push(
    makeCam({name: "อาคารเรียนรวม 1 - ทางเข้า", ip: "192.0.2.11"}),
    makeCam({name: "อาคารเรียนรวม 1 - ลานจอดรถ", ip: "192.0.2.12"}),
    makeCam({name: "ห้องปฏิบัติการ 204", ip: "192.0.2.21"}),
    makeCam({name: "โถงกลาง", ip: "192.0.2.31"}, "offline"),
    makeCam({name: "ห้องประชุม", ip: "192.0.2.41", brand: "hikvision"}, "auth_error"),
  );
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
    return json({error: "ไม่มีในโหมดเดโม"}, 404);
  };
})();
