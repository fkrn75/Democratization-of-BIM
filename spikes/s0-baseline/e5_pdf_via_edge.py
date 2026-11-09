"""E5 관찰: ifcopenshell.draw SVG(A1, 1:100) -> 헤드리스 Edge 인쇄 -> PDF. 벡터 여부·용지 크기·축척을 점검한다.
IfcOpenShell 자체에는 SVG->PDF 변환이 없다 (cairosvg 는 Cairo DLL 필요). Edge 는 사용자 프로필을 건드리지 않게 임시 --user-data-dir 사용.
사용: python e5_pdf_via_edge.py IN.ifc OUTDIR
"""
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import ifcopenshell
import ifcopenshell.draw as d
from pypdf import PdfReader

src, outdir = Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve()
outdir.mkdir(parents=True, exist_ok=True)
f = ifcopenshell.open(str(src))
s = d.draw_settings(auto_floorplan=True, width=841, height=594, scale=1 / 100, cells=False)
svg = d.main(s, files=[f]).decode("utf-8")
# 축척 점검: 평면 SVG 안 모든 path 좌표의 x 범위(mm) -> 12.32m 건물이면 약 123.2mm
xs = []
for m in re.finditer(r' d="([^"]+)"', svg):
    nums = list(map(float, re.findall(r"-?\d+\.?\d*(?:e-?\d+)?", m.group(1))))
    xs += nums[0::2]
print("SVG x 범위 mm: %.1f ~ %.1f (폭 %.1f)" % (min(xs), max(xs), max(xs) - min(xs)), "<- 1:100 이면 외곽 12.32m = 123.2mm 근처여야 함(층 평면이 여러 장 배치되면 더 큼)")
html = ("<!doctype html><meta charset=utf-8><style>@page{size:841mm 594mm;margin:0}html,body{margin:0}</style>"
        + svg[svg.index("<svg"):])
hp = outdir / "A1_floorplan.html"
hp.write_text(html, encoding="utf-8")
pdf = outdir / "A1_floorplan.pdf"
with tempfile.TemporaryDirectory() as ud:
    subprocess.run([r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe", "--headless=new", "--disable-gpu",
                    f"--user-data-dir={ud}", "--no-pdf-header-footer", f"--print-to-pdf={pdf}", hp.as_uri()],
                   check=True, capture_output=True, timeout=120)
r = PdfReader(str(pdf))
pg = r.pages[0]
w, h = float(pg.mediabox.width) / 72 * 25.4, float(pg.mediabox.height) / 72 * 25.4
raw = pg.get_contents().get_data()
print("PDF 쪽수", len(r.pages), "용지 %.0f x %.0f mm" % (w, h), "이미지 XObject 수",
      len((pg.get("/Resources") or {}).get("/XObject", {}) or {}), "콘텐츠 연산자 re/l/m 수", raw.count(b" l\n") + raw.count(b" m\n"))
