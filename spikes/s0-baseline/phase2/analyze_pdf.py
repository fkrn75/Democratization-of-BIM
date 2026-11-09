"""ccad 시트 PDF/SVG 분석(호스트에서 파싱만): 용지 크기, 벡터 여부, 글꼴, 한글 텍스트, 축척."""
import re, sys
from pypdf import PdfReader
W = "out/p2/ccad/work/"
r = PdfReader(W + "S101.pdf")
pg = r.pages[0]
print("쪽수", len(r.pages), "용지 mm %.1f x %.1f" % (float(pg.mediabox.width) / 72 * 25.4, float(pg.mediabox.height) / 72 * 25.4))
res = pg.get("/Resources") or {}
print("XObject(이미지 등):", list((res.get("/XObject") or {}).keys())[:5], "글꼴:", [str(v.get_object().get("/BaseFont")) for v in (res.get("/Font") or {}).values()][:6])
txt = pg.extract_text() or ""
print("추출 텍스트 일부:", txt.replace("\n", " | ")[:300])
print("한글 포함:", bool(re.search("[가-힣]", txt)), "'1층 평면도' 포함:", "평면도" in txt, "'소형 사무소' 포함:", "소형 사무소" in txt)
raw = pg.get_contents().get_data()
print("콘텐츠 스트림 바이트", len(raw), "경로 연산자 l/m/re:", raw.count(b" l\n") + raw.count(b" m\n"), raw.count(b" re\n"))
# 축척: 평면 SVG(뷰 내보내기)에서 선분 x 범위
s = open(W + "before_plan.svg", encoding="utf-8").read()
print(s[:300].replace("\n", " "))
xs = [float(v) for m in re.finditer(r'<line[^>]*?x1="([-\d.]+)"[^>]*?x2="([-\d.]+)"', s) for v in m.groups()]
print("평면 SVG 선분 x 범위 %.1f ~ %.1f 폭 %.1f" % (min(xs), max(xs), max(xs) - min(xs)))
