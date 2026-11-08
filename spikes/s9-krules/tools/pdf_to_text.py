"""(선택) 로컬에 받아 둔 지침 PDF 를 텍스트로 뽑는다. 요구사항 후보를 사람이 읽고 고를 때 쓴 보조 도구.
원문 PDF 와 추출 텍스트는 저장소에 넣지 않는다(공공누리 제4유형). 사용: python tools/pdf_to_text.py in.pdf out.txt
필요: pip install pymupdf
"""
import sys

import pymupdf

src, out = sys.argv[1], sys.argv[2]
doc = pymupdf.open(src)
with open(out, "w", encoding="utf-8") as fp:
    for i, page in enumerate(doc, 1):
        fp.write(f"\n=====PAGE {i}=====\n")
        fp.write(page.get_text())
