"""สร้างหน้าเว็บโหมดเดโมสำหรับ GitHub Pages (ไม่มี backend / ไม่มีกล้องจริง)

ใช้ index.html ตัวจริง แล้วแทรก demo.js (ข้อมูลจำลอง) + แถบแจ้งว่าเป็นเดโม
    python tools/build_pages.py            -> สร้างที่ _site/
    python tools/build_pages.py out_dir
"""
import pathlib
import shutil
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
BANNER = (
    '<div style="background:#7a5b00;color:#fff;padding:8px 16px;font-size:14px;text-align:center">'
    "โหมดเดโม: ข้อมูลและภาพกล้องเป็นของจำลอง ใช้งานจริงต้องรัน <code>python web/app.py</code> "
    "บนเครื่องที่อยู่วงเดียวกับกล้อง</div>"
)


def build(out_dir: pathlib.Path) -> pathlib.Path:
    html = (ROOT / "web" / "static" / "index.html").read_text(encoding="utf-8")
    if html.count("<body>") != 1 or "<script>" not in html:
        raise SystemExit("โครงสร้าง index.html เปลี่ยน แทรกเดโมไม่ได้")
    html = html.replace("<body>", "<body>\n" + BANNER, 1)
    # demo.js ต้องโหลดก่อนสคริปต์หลัก เพื่อดัก fetch / รูปภาพได้ทัน
    html = html.replace("<script>", '<script src="demo.js"></script>\n<script>', 1)
    html = html.replace("<title>Camera Monitor</title>", "<title>Camera Monitor (Demo)</title>", 1)

    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "index.html").write_text(html, encoding="utf-8")
    shutil.copy(ROOT / "web" / "demo" / "demo.js", out_dir / "demo.js")
    (out_dir / ".nojekyll").write_text("", encoding="utf-8")
    return out_dir


if __name__ == "__main__":
    out = build(pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "_site")
    print("สร้างเดโมที่", out)
