# SPDX-License-Identifier: MPL-2.0
"""PDF 한글 글꼴 내장 (1단계 시트).

사용한 글자만 남긴 TrueType 부분집합을 PDF 에 넣는다(CID 글꼴 Type0/CIDFontType2, 인코딩 Identity-H, 글자 복사용 ToUnicode 포함).
- 글꼴은 저장소에 넣지 않는다. 시스템 글꼴 경로를 순서대로 찾는다(환경 변수 DOBIM_PDF_FONT 가 있으면 그 파일을 먼저).
  후보는 SIL OFL 1.1 인 글꼴만 둔다: Noto Sans KR(가변·고정), 나눔고딕. 맑은 고딕 같은 상용 글꼴은 후보에 넣지 않는다.
- 글꼴을 못 찾거나 쓸 수 없으면(윤곽선이 TrueType 이 아님, 내장 금지 비트, fontTools 없음) None 을 돌려준다. 호출자는 Helvetica(ASCII 만)로 내려간다.
- 가변 글꼴은 일반 굵기(wght 400)로 고정한 뒤 부분집합을 만든다(기본 굵기가 가장 가는 100 인 글꼴이 있다).
- 의존성: fonttools(MIT). 순수 모듈이다(ifcopenshell·엔진을 모른다).
"""
from __future__ import annotations

import io
import os
import zlib
from pathlib import Path

ENV_VAR = "DOBIM_PDF_FONT"
_NAMES = ("NotoSansKR-VF.ttf", "NotoSansKR-Regular.ttf", "NotoSansKR[wght].ttf", "NanumGothic.ttf", "NanumGothic-Regular.ttf")
_DIRS = (
    "C:/Windows/Fonts", "~/AppData/Local/Microsoft/Windows/Fonts",                       # 윈도(시스템·사용자)
    "/Library/Fonts", "/System/Library/Fonts", "~/Library/Fonts",                        # macOS
    "/usr/share/fonts/truetype/noto", "/usr/share/fonts/truetype/nanum", "/usr/share/fonts/noto",
    "/usr/share/fonts/opentype/noto", "/usr/local/share/fonts", "~/.local/share/fonts",   # 리눅스
)
EMBED_FORBIDDEN = 0x0002          # OS/2 fsType: 내장 금지(Restricted License)


def find_font_path() -> Path | None:
    """환경 변수 -> 이름 순서(Noto Sans KR 먼저) x 폴더 순서. 없으면 None."""
    env = os.environ.get(ENV_VAR)
    if env:
        p = Path(env).expanduser()
        return p if p.is_file() else None
    for name in _NAMES:
        for d in _DIRS:
            p = Path(d).expanduser() / name
            try:
                if p.is_file():
                    return p
            except OSError:
                continue
    return None


class PdfFont:
    """글꼴 한 벌. show(s) 가 글자를 모으며 Tj 인자를 돌려주고, objects() 가 마지막에 부분집합 글꼴 객체들을 만든다."""

    def __init__(self, path: Path, data: bytes, family: str):
        self.path, self.data, self.family = path, data, family
        self.used: set[str] = set()

    @property
    def name(self) -> str:
        return self.family

    def show(self, s: str) -> str:
        """Tj 앞에 올 16진 문자열. 글자는 objects() 때 모두 모아 한 번에 부분집합으로 만든다(여기서는 글자 -> 임시 번호만 쓴다)."""
        self.used.update(s)
        return "<" + "".join(f"{ord(c):04X}" for c in s) + ">"      # 임시 인코딩: 유니코드 코드포인트(objects 가 GID 로 바꿔 쓴다)

    def finalize_stream(self, content: str) -> bytes:
        """콘텐츠 스트림의 임시 16진 글자열을 GID 로 바꾼다. 부분집합 글꼴과 (사용 글자 -> GID) 표를 만든다."""
        self._build()
        import re

        def sub(m):
            cps = m.group(1)
            return "<" + "".join(f"{self.gid_of.get(chr(int(cps[i:i + 4], 16)), 0):04X}" for i in range(0, len(cps), 4)) + ">"
        return re.sub(r"<([0-9A-F]+)>", sub, content).encode("ascii")

    def _build(self) -> None:
        if hasattr(self, "gid_of"):
            return
        from fontTools import subset
        from fontTools.ttLib import TTFont
        font = TTFont(io.BytesIO(self.data))
        chars = sorted(self.used | {" "})
        opts = subset.Options()
        opts.layout_features = []            # 한글·영문 단순 배치만 쓴다
        opts.hinting = False
        opts.notdef_outline = True
        opts.name_IDs = [1, 2, 3, 4, 6]
        opts.drop_tables += ["DSIG", "GSUB", "GPOS", "GDEF", "kern"]
        sub = subset.Subsetter(opts)
        sub.populate(unicodes=[ord(c) for c in chars])
        sub.subset(font)
        if "fvar" in font:                   # 가변 글꼴: 일반 굵기로 고정(기본값이 Thin 인 글꼴이 있다)
            from fontTools.varLib import instancer
            font = instancer.instantiateVariableFont(font, {"wght": 400}, inplace=True)
        cmap = font.getBestCmap()
        self.gid_of = {c: font.getGlyphID(cmap[ord(c)]) for c in chars if ord(c) in cmap}
        self.missing = sorted(c for c in chars if c not in self.gid_of and c.isprintable() and c != " ")
        upem = font["head"].unitsPerEm
        hmtx = font["hmtx"]
        order = font.getGlyphOrder()
        self.widths = {gid: round(hmtx[order[gid]][0] * 1000 / upem) for gid in set(self.gid_of.values()) | {0}}
        self.bbox = [round(v * 1000 / upem) for v in (font["head"].xMin, font["head"].yMin, font["head"].xMax, font["head"].yMax)]
        self.ascent = round(font["hhea"].ascent * 1000 / upem)
        self.descent = round(font["hhea"].descent * 1000 / upem)
        # PDF 의 FontFile2 는 glyf 기반 TrueType 이다: 이름·cmap 은 남겨도 무방하나 크기를 줄이려 post 이름을 뺀다
        font["post"].formatType = 3.0
        font.recalcTimestamp = False         # 저장 시각을 넣지 않는다(같은 입력 -> 같은 PDF)
        buf = io.BytesIO()
        font.save(buf)
        self.subset_bytes = buf.getvalue()
        self.num_glyphs = len(order)

    def objects(self, first_id: int) -> tuple[list[bytes], int]:
        """PDF 객체 본문 목록(번호 first_id 부터)과 폰트 사전(Type0)의 번호. 번호: Type0, CIDFont, 디스크립터, FontFile2, ToUnicode."""
        self._build()
        t0, cid, desc, ff, tu = (first_id + i for i in range(5))
        base = "DOBIMF+" + "".join(c for c in self.family if c.isalnum())[:30]
        ordered = sorted(self.widths)
        w = " ".join(f"{g} [{self.widths[g]}]" for g in ordered)
        comp = zlib.compress(self.subset_bytes, 9)
        # ToUnicode: GID -> 유니코드(글자 복사·검색용)
        pairs = sorted((g, c) for c, g in self.gid_of.items() if g)
        bf = "\n".join(f"<{g:04X}> <{ord(c):04X}>" for g, c in pairs if ord(c) <= 0xFFFF)
        cmap = ("/CIDInit /ProcSet findresource begin 12 dict begin begincmap /CIDSystemInfo << /Registry (Adobe) /Ordering (UCS) /Supplement 0 >> def "
                "/CMapName /Adobe-Identity-UCS def /CMapType 2 def 1 begincodespacerange <0000> <FFFF> endcodespacerange\n"
                + "".join(f"{len(chunk)} beginbfchar\n" + "\n".join(chunk) + "\nendbfchar\n" for chunk in _chunks(bf.split("\n"), 100) if chunk != [""])
                + "endcmap CMapName currentdict /CMap defineresource pop end end").encode("ascii")
        out = [
            f"<< /Type /Font /Subtype /Type0 /BaseFont /{base} /Encoding /Identity-H /DescendantFonts [{cid} 0 R] /ToUnicode {tu} 0 R >>".encode(),
            (f"<< /Type /Font /Subtype /CIDFontType2 /BaseFont /{base} /CIDSystemInfo << /Registry (Adobe) /Ordering (Identity) /Supplement 0 >> "
             f"/FontDescriptor {desc} 0 R /DW 1000 /W [{w}] /CIDToGIDMap /Identity >>").encode(),
            (f"<< /Type /FontDescriptor /FontName /{base} /Flags 4 /FontBBox [{self.bbox[0]} {self.bbox[1]} {self.bbox[2]} {self.bbox[3]}] "
             f"/ItalicAngle 0 /Ascent {self.ascent} /Descent {self.descent} /CapHeight 700 /StemV 80 /FontFile2 {ff} 0 R >>").encode(),
            f"<< /Length {len(comp)} /Length1 {len(self.subset_bytes)} /Filter /FlateDecode >>\nstream\n".encode() + comp + b"\nendstream",
            f"<< /Length {len(cmap)} >>\nstream\n".encode() + cmap + b"\nendstream",
        ]
        return out, t0


def _chunks(seq, n):
    for i in range(0, len(seq), n):
        yield seq[i:i + n]


_CACHE: dict[str, tuple[bytes, str]] = {}


def load_pdf_font() -> PdfFont | None:
    """PDF 에 내장할 글꼴. 없거나 쓸 수 없으면 None."""
    try:
        import fontTools  # noqa: F401
        from fontTools.ttLib import TTFont
    except ImportError:
        return None
    p = find_font_path()
    if p is None:
        return None
    key = str(p)
    if key not in _CACHE:
        try:
            data = p.read_bytes()
            f = TTFont(io.BytesIO(data), lazy=True)
            if "glyf" not in f or (f["OS/2"].fsType & EMBED_FORBIDDEN):
                return None
            fam = f["name"].getDebugName(1) or p.stem
        except Exception:
            return None
        _CACHE[key] = (data, fam)
    data, fam = _CACHE[key]
    return PdfFont(p, data, fam)
