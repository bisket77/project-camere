import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import build_pages  # noqa: E402


def test_build_pages_inserts_demo_before_main_script(tmp_path):
    out = build_pages.build(tmp_path)
    html = (out / "index.html").read_text(encoding="utf-8")
    assert (out / "demo.js").exists()
    assert html.index('<script src="demo.js"></script>') < html.index("<script>")
    assert "โหมดเดโม" in html and "(Demo)</title>" in html
