# SPDX-License-Identifier: MPL-2.0
"""라이선스 식 평가와 금지 검사 (순수 함수 — 시험 가능).

식은 SPDX 표현식의 부분집합(AND, OR, WITH, 괄호)을 해석한다. 판정은 4단계 (기본은 엄격 — 허용 목록에 없으면 통과하지 못한다):
  ok        허용(관대한 라이선스)
  weak      약한 카피레프트 허용(LGPL·MPL 등). 배포 시 의무가 따른다: 교체 가능성·고지·소스 제공
  forbid    금지(GPL·AGPL·SSPL·비상업 조항 등). 예외절(WITH)이 허용 목록에 있으면 해당 없음
  unknown   식을 해석할 수 없거나 목록에 없다 -> 사람이 확인해야 한다 (gen_notices 는 기본으로 실패 처리한다)
OR 은 가장 덜 위험한 쪽을, AND 는 가장 위험한 쪽을 취한다.
식을 해석할 수 없는 자유 문장이라도 GPL/AGPL 문구(LGPL·Lesser 는 제외)가 있으면 unknown 이 아니라 forbid 로 본다.
메타데이터의 분류자(Classifier)가 여러 개면 OR 가 아니라 AND 로 묶는다: 이중 라이선스인지 알 수 없는 상태에서 OR 로 묶으면 GPL 이 가려지기 때문이다.
"""
from __future__ import annotations

import re

RANK = {"ok": 0, "weak": 1, "unknown": 2, "forbid": 3}

OK_IDS = {
    "bzip2-1.0.6", "Unicode-3.0", "Unicode-DFS-2016",
    "MIT", "MIT-0", "BSD-2-Clause", "BSD-3-Clause", "BSD-3-Clause-Clear", "Apache-2.0", "ISC", "PSF-2.0", "Python-2.0",
    "Zlib", "0BSD", "BSL-1.0", "CC0-1.0", "Unlicense", "HPND", "blessing", "Public-Domain", "LicenseRef-public-domain",
    "BSD-2-Clause-Views", "NCSA", "X11", "OpenSSL", "Python-2.0.1", "CNRI-Python", "Apache-2.0-with-LLVM-exception",
    "MIT-CMU",
}
WEAK_PREFIX = ("LGPL-", "MPL-", "EPL-", "CDDL-", "CPL-")
FORBID_PREFIX = ("GPL-", "AGPL-", "SSPL-", "CC-BY-NC", "CC-BY-ND", "Commons-Clause", "BUSL-", "Elastic-", "OSL-")
# 예외절이 붙으면 금지에서 풀어 주는 (라이선스, 예외절) 쌍. 근거는 components 쪽 정책 표에 적는다
ALLOWED_WITH = {
    ("GPL-2.0-or-later", "Bootloader-exception"),            # PyInstaller 부트로더: 어떤 라이선스의 앱에도 쓸 수 있는 예외
    ("GPL-3.0-or-later", "GCC-exception-3.1"),               # GCC 런타임 라이브러리 예외
    ("GPL-3.0-only", "GCC-exception-3.1"),
    ("LGPL-2.1-only", "OCCT-exception-1.0"),
    ("LGPL-2.1-or-later", "OCCT-exception-1.0"),
}
# SPDX 가 아닌 흔한 옛 표기 -> SPDX
ALIASES = {
    "MIT LICENSE": "MIT", "THE MIT LICENSE": "MIT", "BSD": "BSD-3-Clause", "BSD LICENSE": "BSD-3-Clause",
    "BSD-3": "BSD-3-Clause", "3-CLAUSE BSD": "BSD-3-Clause", "APACHE 2.0": "Apache-2.0", "APACHE-2.0": "Apache-2.0",
    "APACHE LICENSE 2.0": "Apache-2.0", "APACHE SOFTWARE LICENSE": "Apache-2.0", "APACHE LICENSE, VERSION 2.0": "Apache-2.0",
    "PSF": "PSF-2.0", "PYTHON SOFTWARE FOUNDATION LICENSE": "PSF-2.0", "MPL-2.0": "MPL-2.0",
    "LGPL": "LGPL-3.0-or-later", "LGPL-3.0": "LGPL-3.0-or-later", "LGPLV3": "LGPL-3.0-or-later",
    "GNU LESSER GENERAL PUBLIC LICENSE V3 (LGPLV3)": "LGPL-3.0-only",
}
CLASSIFIERS = {
    "MIT License": "MIT", "BSD License": "BSD-3-Clause", "Apache Software License": "Apache-2.0",
    "ISC License (ISCL)": "ISC", "Python Software Foundation License": "PSF-2.0",
    "Mozilla Public License 2.0 (MPL 2.0)": "MPL-2.0", "The Unlicense (Unlicense)": "Unlicense",
    "GNU Lesser General Public License v3 (LGPLv3)": "LGPL-3.0-only",
    "GNU Lesser General Public License v2 or later (LGPLv2+)": "LGPL-2.1-or-later",
    "GNU General Public License v3 (GPLv3)": "GPL-3.0-only", "GNU General Public License v2 (GPLv2)": "GPL-2.0-only",
    "GNU General Public License v3 or later (GPLv3+)": "GPL-3.0-or-later",
    "GNU Affero General Public License v3": "AGPL-3.0-only", "Public Domain": "LicenseRef-public-domain",
    "Zope Public License": "ZPL-2.1", "Other/Proprietary License": "LicenseRef-proprietary",
}

_TOKEN = re.compile(r"\(|\)|[A-Za-z0-9.+\-:]+")


WEAK_IDS = {"LicenseRef-MS-Redistributable"}   # 마이크로소프트 재배포 가능 런타임(비 OSS, 재배포 조항을 따른다)


# 자유 문장에서 GPL 계열을 찾는다. LGPL·GNU Lesser/Library GPL 은 먼저 지워서 GPL 로 오인하지 않는다
_LESSER_RX = re.compile(r"(?i)LGPL[-\w.+]*|GNU\s+(?:Lesser|Library)\s+General\s+Public\s+Licen[sc]e|(?:Lesser|Library)\s+General\s+Public\s+Licen[sc]e")
_GPL_RX = re.compile(r"(?i)(?<![a-z])a?gpl|general\s+public\s+licen[sc]e|gnu\s+gpl")


def text_is_gpl(text: str) -> bool:
    """자유 문장/식에 GPL·AGPL 문구가 있는가 (LGPL·Lesser 는 제외)."""
    return bool(_GPL_RX.search(_LESSER_RX.sub(" ", text or "")))


def classify_id(lic: str) -> str:
    base = lic.rstrip("+")
    if lic in WEAK_IDS:
        return "weak"
    if lic in OK_IDS or base in OK_IDS:
        return "ok"
    if lic.startswith(FORBID_PREFIX) or re.match(r"(?i)^a?gpl(?![a-z]*(?<=lgpl))", lic):
        return "forbid"
    if lic.startswith(WEAK_PREFIX):
        return "weak"
    return "unknown"


def _atom(tokens: list[str], i: int) -> tuple[str, str, int]:
    """한 리프(라이선스 [WITH 예외]) 또는 괄호 식. 반환: (판정, 설명, 다음 위치)"""
    if i >= len(tokens):
        return "unknown", "식이 비었다", i
    if tokens[i] == "(":
        v, d, i = _or(tokens, i + 1)
        if i < len(tokens) and tokens[i] == ")":
            i += 1
        return v, d, i
    lic = ALIASES.get(tokens[i].upper(), tokens[i])
    i += 1
    exc = None
    if i + 1 < len(tokens) and tokens[i].upper() == "WITH":
        exc = tokens[i + 1]
        i += 2
    v = classify_id(lic)
    if exc is not None:
        if v == "forbid" and (lic, exc) in ALLOWED_WITH:
            v = "weak"   # 예외절로 풀렸다: 그래도 의무(고지 등)는 따른다
        elif v == "forbid":
            return "forbid", f"{lic} WITH {exc}(허용 목록에 없는 예외)", i
        return v, f"{lic} WITH {exc}", i
    return v, lic, i


def _and(tokens, i):
    v, d, i = _atom(tokens, i)
    while i < len(tokens) and tokens[i].upper() == "AND":
        v2, d2, i = _atom(tokens, i + 1)
        if RANK[v2] > RANK[v]:
            v = v2
        d = f"{d} AND {d2}"
    return v, d, i


def _or(tokens, i):
    v, d, i = _and(tokens, i)
    best = (v, d)
    while i < len(tokens) and tokens[i].upper() == "OR":
        v2, d2, i = _and(tokens, i + 1)
        if RANK[v2] < RANK[best[0]]:
            best = (v2, d2)
    return best[0], best[1], i


def evaluate(expr: str) -> tuple[str, str]:
    """(판정, 선택된 대안 설명). 해석 불가 입력은 unknown."""
    expr = (expr or "").strip()
    if not expr or expr.lower() in ("unknown", "미확인"):
        return "unknown", "라이선스 정보 없음"
    tokens = _TOKEN.findall(expr)
    if not tokens or len(" ".join(tokens).replace(" ", "")) < len(expr.replace(" ", "")) - 2:
        return _unparsed(expr)
    v, d, end = _or(tokens, 0)
    if end < len(tokens):
        return _unparsed(expr)
    return v, d


def _unparsed(expr: str) -> tuple[str, str]:
    """식으로 해석하지 못한 입력: GPL 문구가 있으면 forbid, 아니면 unknown (조용히 통과시키지 않는다)."""
    if text_is_gpl(expr):
        return "forbid", f"GPL 계열 문구가 있는 해석 불가 표기: {expr[:60]}"
    return "unknown", f"해석 불가 표기: {expr[:60]}"


# 바이너리 배포에서 의미가 없는 일반 분류자(라이선스가 아니다)
_NEUTRAL_CLASSIFIERS = {"OSI Approved", "DFSG approved", "Freely Distributable", "Free For Educational Use", "Free For Home Use"}


def from_classifiers(classifiers: list[str]) -> tuple[str, str]:
    """`License :: ...` 분류자 목록 -> (SPDX 식, 근거). 여러 개는 AND, 모르는 분류자는 unknown 이 되게 둔다."""
    ids = []
    for c in classifiers:
        name = c.split("::")[-1].strip()
        if name in _NEUTRAL_CLASSIFIERS:
            continue
        ids.append(CLASSIFIERS.get(name) or "LicenseRef-unmapped-classifier-" + re.sub(r"[^A-Za-z0-9]+", "-", name).strip("-"))
    ids = sorted(set(ids))
    if not ids:
        return "", "분류자 없음"
    if len(ids) == 1:
        return ids[0], "metadata classifier"
    return " AND ".join(ids), "metadata classifier 여러 개 -> AND(보수적, 이중 라이선스면 DIST_OVERRIDES 로 명시)"
