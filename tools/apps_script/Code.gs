/**
 * Google Apps Script: ส่งรายการกล้อง/โปรเจกเตอร์จาก Sheet ให้เว็บ Classroom Monitor (Pi) ดึงไป
 *
 * ทำไมให้ Pi "ดึง" แทน Google "ส่ง": Pi อยู่ในวงแลน/ZeroTier ที่ Google เข้าไม่ถึง
 * - แก้ Sheet → trigger onSheetChange เปลี่ยนเลข VERSION
 * - Pi ถามทุก 30 วินาทีพร้อม version ล่าสุดที่รู้ ถ้าไม่เปลี่ยนจะตอบสั้น ๆ {unchanged:true}
 *
 * ติดตั้ง:
 * 1) ใน Sheet: Extensions → Apps Script → วางไฟล์นี้แทน Code.gs
 * 2) Project Settings → Script properties → เพิ่ม SYNC_TOKEN = (สุ่มยาว ๆ อย่างน้อย 32 ตัวอักษร)
 * 3) เลือกฟังก์ชัน setup แล้วกด Run หนึ่งครั้ง (อนุญาตสิทธิ์) → สร้าง trigger ตอนแก้ไข
 * 4) Deploy → New deployment → Web app: Execute as = Me, Who has access = Anyone
 *    คัดลอก URL .../exec ไปใส่บน Pi (ตัวแปร SHEET_URL)
 */
const SHEET_NAME = 'CCTV & Projector';
const HEADER_ROW = 2;

function setup() {
  ScriptApp.getProjectTriggers().forEach(t => ScriptApp.deleteTrigger(t));
  const ss = SpreadsheetApp.getActive();
  ScriptApp.newTrigger('onSheetChange').forSpreadsheet(ss).onEdit().create();
  ScriptApp.newTrigger('onSheetChange').forSpreadsheet(ss).onChange().create();
  onSheetChange();
}

function onSheetChange() {
  PropertiesService.getScriptProperties().setProperty('VERSION', String(Date.now()));
}

function doGet(e) {
  const props = PropertiesService.getScriptProperties();
  const token = props.getProperty('SYNC_TOKEN');
  if (!token || (e.parameter.token || '') !== token) return json({ error: 'unauthorized' });
  const version = props.getProperty('VERSION') || '0';
  if (e.parameter.v === version) return json({ unchanged: true, version: version });

  const values = SpreadsheetApp.getActive().getSheetByName(SHEET_NAME).getDataRange().getDisplayValues();
  const head = values[HEADER_ROW - 1].map(h => String(h).trim());
  const get = (r, name) => {
    const i = head.indexOf(name);
    return i >= 0 ? String(r[i] || '').trim() : '';
  };
  const rows = [];
  for (let i = HEADER_ROW; i < values.length; i++) {
    const r = values[i];
    const type = get(r, 'Name');
    const ip = get(r, 'IP');
    if (!type || !ip) continue; // แถวว่าง
    rows.push({
      no: get(r, 'ลำดับ'), type: type, mac: get(r, 'MAC-address'), model: get(r, 'Type'), ip: ip,
      user: get(r, 'Username'), password: get(r, 'Password'), port: get(r, 'Port'),
      pjlink_port: get(r, 'PJLink Port'), control_port: get(r, 'Control Port'),
      building: get(r, 'Location'), room: get(r, 'Room'), floor: get(r, 'ชั้น'),
      rtsp: get(r, 'RTSP URL'), // ไม่บังคับ: ใส่เองเมื่อสร้าง URL อัตโนมัติไม่ได้ (เช่น UniFi)
    });
  }
  return json({ version: version, rows: rows });
}

function json(obj) {
  return ContentService.createTextOutput(JSON.stringify(obj)).setMimeType(ContentService.MimeType.JSON);
}
