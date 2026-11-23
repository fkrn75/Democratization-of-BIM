# SPDX-License-Identifier: MPL-2.0
"""외래 IFC 가져오기 계획 (S2, ADR-0005): 들어온 파일의 요소를 authored / foreign / detached 로 분류한다.

분류 규칙(요소 = 코어 뼈대가 아닌 IfcProduct):
- DoBIM_Intent 가 있고, 현재 코어 스키마로 해석되고(레벨·벽·문, 패밀리 타입·인스턴스: ADR-0014), 가리키는 대상(레벨·호스트·조인 상대, 인스턴스의 타입)이
  있으면 의도를 복원해 후보가 된다. 패밀리 후보는 레시피·값을 S4 의 규칙으로 평가해 일관될 때만 후보가 된다(family.restore).
- 후보의 의도를 새 IFC 모델에 다시 투영해, 요소 단위 투영 클로저 해시(ifc_model.Closure)를 들어온 요소의 해시와 비교한다.
  같으면 authored(의도가 형상의 정본), 다르면 detached(들어온 형상을 그대로 동결, 마지막 의도는 낡은 사본으로 남는다).
- 후보가 아니면 foreign(IFC 가 형상의 정본, 속성만 편집). 패밀리 타입(IfcTypeObject)은 후보일 때만 요소(ftype)가 되고, 아니면 요소가 아닌 채 파일에 남는다.
- 분리된 패밀리 타입의 인스턴스는 타입이 낡은 정의이므로 함께 분리한다(인스턴스를 다시 투영하면 변조된 타입의 표현 지도를 가리키게 된다).
- 길이 단위가 밀리미터가 아니면(의도의 값은 mm 다) 후보 전체를 detached 로 둔다: 단위는 프로젝트 수준의 변경이라 요소 하나하나의 클로저에
  나타나지 않기 때문이다. 분리된 레벨이 있으면 그 위의 저작 요소가 레벨 배치를 따라 움직인다는 경고를 더한다.
- 뷰·치수·태그(IFC 신원이 없는 의도 요소)의 정의는 프로젝트의 속성셋 DoBIM_Defs 에 실려 있다(ADR-0015). 정의마다 viewdefs.parse_def 로 검증하고(모양·범위·변조 해시),
  가리키는 레벨·뷰·대상이 파일 안에서 어떤 상태인지 보고 되살린다. 레벨·벽·문이 저작이면 그대로, 파일에 없으면 고아로(커맨드로 지운 뒤와 같다),
  있는데 저작이 아니면(외래·분리) 되살리지 않는다(낡은 값을 재게 된다). 되살리지 않은 정의는 파일에 그대로 남고 내보낼 때도 건드리지 않는다.
- 뼈대(Site/Building)는 구조·이름으로 알아보고 위경도·주소 같은 비형상 속성은 보지 않는다. 뼈대의 형상이 바뀌었으면 경고한다(ifc_model.skeleton_check).

이 모듈은 파일을 읽어 계획(ImportPlan)을 만들 뿐 프로젝트를 만들지 않는다. 엔진이 계획을 SQLite 에 적는다.
"""
from __future__ import annotations

import json
import math
import re
import uuid
from dataclasses import dataclass, field

import ifcopenshell
import ifcopenshell.util.element as ue
import ifcopenshell.util.unit as uunit

from dobim_core import queries as Q
from dobim_core.errors import DobimError
from dobim_core.graph import Graph
from dobim_core.family import restore as FR
from dobim_core import annot as AN
from dobim_core import kinds as K
from dobim_core import viewdefs as VD
from dobim_core.kinds import ELEMENT_KINDS, FAMILY_OF_KIND, IMPORTABLE_BY_CLASS
from dobim_core.ifc_model import (INTENT_PSET, Closure, IfcModel, closure_diff, open_ifc_bytes, skeleton_check)

MAX_IMPORT_BYTES = 256 * 1024 * 1024
MAX_MM = 1e9   # 의도 숫자의 상한(1000 km). 이보다 큰 값은 제곱하는 계산이 넘치므로 해석하지 않고 외래로 둔다
KIND_OF = IMPORTABLE_BY_CLASS   # 요소 종류 표의 정본은 kinds.py 다(의도를 복원할 수 있는 종류만 뽑은 것)
FAMILY = FAMILY_OF_KIND
K_ELEV_OPT = ("pit", "machine", "doors", "stops", "hole_slabs", "hole_clearance")   # 승강기의 선택 파라미터(commands.ELEV_OPT 와 같다)
_ID = re.compile(r"^[0-9A-Za-z_$]{22}$")
NAME_MAX = 255


@dataclass
class ImportPlan:
    schema: str
    # {id, kind, ifc_class, origin, family_id, params, projection_hash, spec_hash}
    # projection_hash = 가져오는 순간 들어온 요소의 클로저 해시(불변, 조회용). 변조 판정은 가져오는 순간 한 번뿐이다: 이후 외부 파일이 바뀌면
    # 다시 가져와야 알 수 있다. spec_hash = 가져오는 순간 저작 요소의 투영 명세 해시(명세가 그대로면 첫 투영에서 들어온 엔티티를 그대로 둔다).
    rows: list[dict] = field(default_factory=list)
    rels: list[tuple] = field(default_factory=list)      # (kind, src, dst)
    warnings: list[str] = field(default_factory=list)
    detached: list[dict] = field(default_factory=list)   # {id, reasons}
    skeleton: bool = False
    skipped: int = 0                                      # 요소로 세지 않은 제품 수(저작 문의 개구부)

    def counts(self) -> dict:
        c = {"authored": 0, "foreign": 0, "detached": 0}
        for r in self.rows:
            c[r["origin"]] += 1
        return c


# ---------------------------------------------------------------- 의도 해석
def _num(v, *, pos=False, nonneg=False):
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        raise ValueError("유한한 숫자가 아니다")
    v = float(v)
    if abs(v) > MAX_MM:
        raise ValueError("값이 범위(±1e9)를 벗어났다")
    if pos and not v > 0:
        raise ValueError("0 보다 커야 한다")
    if nonneg and v < 0:
        raise ValueError("0 이상이어야 한다")
    return v


def _name(v):
    if not isinstance(v, str) or not 1 <= len(v) <= 64 or "\x00" in v or any(0xD800 <= ord(c) <= 0xDFFF for c in v):
        raise ValueError("이름이 올바르지 않다")
    return v


def _layers(v, total: float):
    """층 구성(선택 파라미터) 해석: [{"material", "thickness"}, ...] 를 저작 행과 같은 모양으로. 두께 합이 요소 두께와 같아야 한다."""
    if not isinstance(v, list) or not 1 <= len(v) <= K.LAYERS_MAX:
        raise ValueError("층 구성이 올바르지 않다")
    out = []
    for x in v:
        if not isinstance(x, dict) or set(x) != {"material", "thickness"}:
            raise ValueError("층 구성 항목이 올바르지 않다")
        out.append({"material": _name(x["material"]), "thickness": _num(x["thickness"], pos=True)})
    if abs(K.layers_total(out) - total) > K.LAYER_SUM_TOL:
        raise ValueError("층 두께의 합이 요소 두께와 다르다")
    return out


def _openings(v) -> list:
    if not isinstance(v, list):
        raise ValueError("개구부 목록이 올바르지 않다")
    out = []
    for o in v:
        if not isinstance(o, dict):
            raise ValueError("개구부가 객체가 아니다")
        d = {k: (_num(x) if k in ("offset", "sill", "width", "height", "rise") else x) for k, x in o.items()}
        out.append(d)
    return out


def _curtwall_intent(p) -> dict:
    """커튼월 의도 params(W2 ARC-22)를 저작 행 모양으로(숫자는 모두 float). 값 검사는 코어 행 검사가 한다."""
    if not isinstance(p, dict):
        raise ValueError("params 가 객체가 아니다")

    def fl(v):
        if isinstance(v, list):
            return [fl(x) for x in v]
        if isinstance(v, dict):
            return {k: fl(x) for k, x in v.items()}
        return _num(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else v
    return {k: fl(v) for k, v in p.items()}


def _solid_intent(p) -> dict:
    """솔리드 의도 params 를 저작 행 모양으로(숫자 float, 면 번호 int)."""
    if not isinstance(p, dict):
        raise ValueError("params 가 객체가 아니다")

    def fl(v):
        if isinstance(v, list):
            return [fl(x) for x in v]
        if isinstance(v, dict):
            return {k: fl(x) for k, x in v.items()}
        return _num(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else v
    out = {}
    for k, v in p.items():
        if k == "faces":
            if not isinstance(v, list) or any(not isinstance(f, list) or any(type(i) is not int for i in f) for f in v):
                raise ValueError("면 번호가 정수가 아니다")
            out[k] = [list(f) for f in v]
        elif k == "openings":
            out[k] = _openings(v)
        elif k == "ops":   # 불리언 피연산자(W2 여섯 번째 PR): 피연산자도 같은 규칙(메시 면 번호 정수, 개구부)
            if not isinstance(v, list) or any(not isinstance(o, dict) for o in v):
                raise ValueError("불리언 목록이 올바르지 않다")
            out[k] = [{kk: (_solid_intent(x) if kk == "solid" else x) for kk, x in o.items()} for o in v]
        elif k == "res" and isinstance(v, dict) and "seg" in v:   # 현 수는 정수
            sg = v["seg"]
            out[k] = {kk: x for kk, x in v.items()}
            if isinstance(sg, float) and sg.is_integer():
                out[k]["seg"] = int(sg)
        else:
            out[k] = fl(v)
    return out


def _parse_intent(kind: str, text: str) -> dict:
    """DoBIM_Intent 텍스트를 현재 코어 스키마로 해석한다. 못 하면 ValueError. 숫자는 float 로 맞춘다(저작 행과 같은 모양)."""
    d = json.loads(text)
    extra = {"level": (), "wall": ("level", "joins"), "door": ("host",), "slab": ("level",), "stair": ("level",), "space": ("level",), "railing": ("level",), "elevator": ("level",), "roof": ("level",), "site": (), "solid": ("level",), "curtwall": ("level",), "stackwall": ("level",)}[kind]
    if not isinstance(d, dict) or set(d) != {"kind", "params", "family_id", *extra} or d["kind"] != kind or d["family_id"] != FAMILY[kind]:
        raise ValueError("의도 모양이 현재 스키마와 다르다")
    p = d["params"]
    if not isinstance(p, dict):
        raise ValueError("params 가 객체가 아니다")
    if kind == "level":
        keys, out = {"name", "elevation"}, None
        if set(p) != keys:
            raise ValueError("레벨 params 키가 다르다")
        out = {"name": _name(p["name"]), "elevation": _num(p["elevation"])}
    elif kind == "wall":
        if set(p) - {"layers", "openings", "top_profile", "base_offset", "cuts", "top_level", "top_offset", "tee", "arc", "wtype", "loc", "core", "stack"} != {"name", "axis", "thickness", "height"} or not isinstance(p["axis"], list) or len(p["axis"]) != 4:
            raise ValueError("벽 params 가 다르다")
        axis = [_num(x) for x in p["axis"]]
        if (axis[2] - axis[0]) ** 2 + (axis[3] - axis[1]) ** 2 < 1.0:
            raise ValueError("벽 길이가 1mm 미만이다")
        out = {"name": _name(p["name"]), "axis": axis, "thickness": _num(p["thickness"], pos=True), "height": _num(p["height"], pos=True)}
        if "layers" in p:
            out["layers"] = _layers(p["layers"], out["thickness"])
        if "openings" in p:   # 모양 개구부(18단계): 값 검사는 코어 행 검사와 같다
            out["openings"] = _openings(p["openings"])
        if "top_profile" in p:   # 벽 상단 윤곽(21단계)
            v = p["top_profile"]
            if not isinstance(v, list) or any(not isinstance(q, list) or len(q) != 2 for q in v):
                raise ValueError("벽 상단 윤곽이 올바르지 않다")
            out["top_profile"] = [[_num(q[0]), _num(q[1])] for q in v]
        if "base_offset" in p:   # 벽 바닥 오프셋(22단계): 0 은 키 없음과 같으므로 받지 않는다(저작 행에는 0 이 없다)
            out["base_offset"] = _num(p["base_offset"])
            if out["base_offset"] == 0.0:
                raise ValueError("벽 바닥 오프셋 0 은 키 없음으로 적는다")
        if "cuts" in p:   # 벽 잘라내기(23단계): 상자 6 수 목록(값 검사는 코어 행 검사와 같다)
            v = p["cuts"]
            if not isinstance(v, list) or any(not isinstance(q, list) or len(q) != 6 for q in v):
                raise ValueError("벽 잘라내기 상자가 올바르지 않다")
            out["cuts"] = [[_num(x) for x in q] for q in v]
        if "top_level" in p:   # 상단 구속(24단계): 상단 레벨 id 와 0 이 아닌 오프셋(높이는 저장값이 정본)
            if not K.id_ok(p["top_level"]):
                raise ValueError("벽 상단 레벨 id 가 올바르지 않다")
            out["top_level"] = p["top_level"]
        if "top_offset" in p:
            out["top_offset"] = _num(p["top_offset"])
            if out["top_offset"] == 0.0:
                raise ValueError("벽 상단 오프셋 0 은 키 없음으로 적는다")
        if "tee" in p:   # T 조인 줄기 끝(W1 1-F 3차)
            if not K.tee_ok(p["tee"]):
                raise ValueError("벽 T 조인 표식이 올바르지 않다")
            out["tee"] = list(p["tee"])
        if "arc" in p:   # 곡선(원호) 벽(W2 ARC-04): 호 위의 점
            v = p["arc"]
            if not isinstance(v, list) or len(v) != 2:
                raise ValueError("곡선 벽 호 위의 점이 올바르지 않다")
            out["arc"] = [_num(v[0]), _num(v[1])]
        for k in ("wtype", "loc", "core", "stack"):   # 벽 유형·위치선·코어 범위·스택 벽 부분(W2 일곱 번째 PR): 값 검사는 코어 행 검사가 한다
            if k in p:
                out[k] = list(p[k]) if isinstance(p[k], list) else p[k]
        if "openings" in p or "top_profile" in p or "base_offset" in p or "cuts" in p or "top_level" in p or "top_offset" in p or "arc" in p or any(k in p for k in ("wtype", "loc", "core", "stack")):
            why = K.row_params_error("wall", out)
            if why:
                raise ValueError(f"벽이 올바르지 않다({why})")
    elif kind == "stackwall":   # 스택 벽(W2 일곱 번째 PR): 숫자를 float 로 맞춘 뒤 코어 행 검사
        out = _curtwall_intent(p)
        why = K.row_params_error("stackwall", out)
        if why:
            raise ValueError(f"스택 벽이 올바르지 않다({why})")
    elif kind == "curtwall":   # 커튼월(W2 ARC-22): 숫자를 float 로 맞춘 뒤 코어 행 검사
        out = _curtwall_intent(p)
        why = K.row_params_error("curtwall", out)
        if why:
            raise ValueError(f"커튼월이 올바르지 않다({why})")
    elif kind == "solid":   # 일반 솔리드(18단계): 숫자를 float 로(면 번호는 정수 그대로) 맞춘 뒤 코어 행 검사
        out = _solid_intent(p)
        why = K.row_params_error("solid", out)
        if why:
            raise ValueError(f"솔리드가 올바르지 않다({why})")
    elif kind == "slab":
        if set(p) - {"layers", "openings", "use", "fall", "drains", "parapet"} != {"name", "boundary", "thickness", "top_offset"} or not isinstance(p["boundary"], list) or not 3 <= len(p["boundary"]) <= 256:
            raise ValueError("슬래브 params 가 다르다")
        pts = []
        for q in p["boundary"]:
            if not isinstance(q, list) or len(q) != 2:
                raise ValueError("슬래브 경계 꼭짓점이 올바르지 않다")
            pts.append([_num(q[0]), _num(q[1])])
        why = K.polygon_problem(pts)
        if why or K.polygon_area2(pts) <= 0:   # 의도는 반시계로 저장한다(slab.create 가 정규화)
            raise ValueError(f"슬래브 경계가 올바르지 않다({why or '시계 방향'})")
        out = {"name": _name(p["name"]), "boundary": pts, "thickness": _num(p["thickness"], pos=True), "top_offset": _num(p["top_offset"])}
        if "layers" in p:
            out["layers"] = _layers(p["layers"], out["thickness"])
        if "openings" in p:
            ops = p["openings"]
            if not isinstance(ops, list) or not 1 <= len(ops) <= K.SLAB_OPENINGS_MAX:
                raise ValueError("슬래브 구멍 목록이 올바르지 않다")
            conv = []
            for o in ops:
                if not isinstance(o, list) or not 3 <= len(o) <= 256 or any(not isinstance(q, list) or len(q) != 2 for q in o):
                    raise ValueError("슬래브 구멍 꼭짓점이 올바르지 않다")
                conv.append([[_num(q[0]), _num(q[1])] for q in o])
            if any(K.polygon_area2(o) <= 0 for o in conv):
                raise ValueError("슬래브 구멍은 반시계로 저장한다")
            why = K.openings_problem(pts, conv)
            if why:
                raise ValueError(f"슬래브 구멍이 올바르지 않다({why})")
            out["openings"] = conv
        if "use" in p:   # 지붕 슬래브: 물매·배수구는 지붕에만 있다(검사는 코어 행 검사와 같은 것)
            out["use"] = p["use"]
            if "fall" in p:
                if not isinstance(p["fall"], dict) or set(p["fall"]) != {"dir", "pct"}:
                    raise ValueError("슬래브 물매가 올바르지 않다")
                out["fall"] = {"dir": _num(p["fall"]["dir"]), "pct": _num(p["fall"]["pct"])}
            if "drains" in p:
                if not isinstance(p["drains"], list) or any(not isinstance(q, list) or len(q) != 2 for q in p["drains"]):
                    raise ValueError("슬래브 배수구가 올바르지 않다")
                out["drains"] = [[_num(q[0]), _num(q[1])] for q in p["drains"]]
            if "parapet" in p:
                if not isinstance(p["parapet"], dict) or set(p["parapet"]) != {"height", "thickness"}:
                    raise ValueError("슬래브 파라펫이 올바르지 않다")
                out["parapet"] = {"height": _num(p["parapet"]["height"]), "thickness": _num(p["parapet"]["thickness"])}
            why = K.row_params_error("slab", out)   # 배수구가 슬래브 위에 있는지 등(코어 행 검사와 같은 것)
            if why:
                raise ValueError(f"슬래브가 올바르지 않다({why})")
        elif "fall" in p or "drains" in p or "parapet" in p:
            raise ValueError("물매·배수구·파라펫은 지붕 슬래브에만 있다")
    elif kind == "space":
        if (set(p) - {"ceiling_height", "holes"} != {"name", "category", "boundary", "height"} or not isinstance(p["boundary"], list) or not 3 <= len(p["boundary"]) <= 256
                or not isinstance(p["category"], str) or len(p["category"]) > 255):
            raise ValueError("공간 params 가 다르다")
        pts = []
        for q in p["boundary"]:
            if not isinstance(q, list) or len(q) != 2:
                raise ValueError("공간 경계 꼭짓점이 올바르지 않다")
            pts.append([_num(q[0]), _num(q[1])])
        why = K.polygon_problem(pts)
        if why or K.polygon_area2(pts) <= 0:
            raise ValueError(f"공간 경계가 올바르지 않다({why or '시계 방향'})")
        out = {"name": _name(p["name"]), "category": p["category"], "boundary": pts, "height": _num(p["height"], pos=True)}
        if "ceiling_height" in p:
            out["ceiling_height"] = _num(p["ceiling_height"], pos=True)
            if out["ceiling_height"] > out["height"]:
                raise ValueError("천장 높이가 공간 높이보다 크다")
        if "holes" in p:   # 섬 구멍(15단계): 슬래브 구멍과 같은 모양·검사
            hs = p["holes"]
            if not isinstance(hs, list) or not 1 <= len(hs) <= K.SLAB_OPENINGS_MAX:
                raise ValueError("공간 구멍 목록이 올바르지 않다")
            conv = []
            for o in hs:
                if not isinstance(o, list) or not 3 <= len(o) <= 256 or any(not isinstance(q, list) or len(q) != 2 for q in o):
                    raise ValueError("공간 구멍 꼭짓점이 올바르지 않다")
                conv.append([[_num(q[0]), _num(q[1])] for q in o])
            if any(K.polygon_area2(o) <= 0 for o in conv):
                raise ValueError("공간 구멍은 반시계로 저장한다")
            why = K.openings_problem(pts, conv)
            if why:
                raise ValueError(f"공간 구멍이 올바르지 않다({why})")
            out["holes"] = conv
    elif kind == "site":
        if (set(p) - set(K.SITE_OPTIONAL) != {"name", "boundary", "north_angle", "roads", "elevation"} or not isinstance(p["boundary"], list) or not 3 <= len(p["boundary"]) <= 256
                or not isinstance(p["roads"], list) or len(p["roads"]) > K.SITE_ROADS_MAX):
            raise ValueError("대지 params 가 다르다")
        pts = []
        for q in p["boundary"]:
            if not isinstance(q, list) or len(q) != 2:
                raise ValueError("대지 경계 꼭짓점이 올바르지 않다")
            pts.append([_num(q[0]), _num(q[1])])
        why = K.polygon_problem(pts)
        if why or K.polygon_area2(pts) <= 0:
            raise ValueError(f"대지 경계가 올바르지 않다({why or '시계 방향'})")
        roads = []
        for r in p["roads"]:
            if (not isinstance(r, dict) or set(r) != {"name", "edge", "width"} or not isinstance(r["name"], str) or len(r["name"]) > 64
                    or type(r["edge"]) is not int or not 0 <= r["edge"] < len(pts)):
                raise ValueError("대지 도로가 올바르지 않다")
            roads.append({"name": r["name"], "edge": r["edge"], "width": _num(r["width"], pos=True)})
        na = _num(p["north_angle"])
        if not -360.0 <= na <= 360.0:
            raise ValueError("대지 방위 각이 범위를 벗어났다")
        out = {"name": _name(p["name"]), "boundary": pts, "north_angle": na, "roads": roads,
               "elevation": None if p["elevation"] is None else _num(p["elevation"])}
        if len({r["edge"] for r in roads}) != len(roads):
            raise ValueError("한 변에 도로가 둘 이상이다")
        for k in K.SITE_OPTIONAL:   # 지반 표고점·조경 영역·주차 계획·간판(14절): 있으면 코어 행 검사와 같은 검사로 읽는다
            if k in p:
                out[k] = K.norm_site_extra(p[k])
        why = K.row_params_error("site", out)
        if why:
            raise ValueError(f"대지가 올바르지 않다({why})")
    elif kind == "railing":
        if (set(p) - {"src", "src_tag", "src_inset"} != {"name", "path", "height", "base_offset", "rise"} or not isinstance(p["path"], list) or not 2 <= len(p["path"]) <= K.RAILING_PTS_MAX
                or any(not isinstance(q, list) or len(q) != 2 for q in p["path"])):
            raise ValueError("난간 params 가 다르다")
        out = {"name": _name(p["name"]), "path": [[_num(q[0]), _num(q[1])] for q in p["path"]], "height": _num(p["height"], pos=True),
               "base_offset": _num(p["base_offset"]), "rise": _num(p["rise"])}
        if "src" in p:   # 원본(계단·슬래브) 출처 표식(15단계 연동): 값 검사는 코어 행 검사와 같은 것이다
            if not isinstance(p["src"], str) or not _ID.match(p["src"]) or not isinstance(p.get("src_tag"), str):
                raise ValueError("난간 출처가 올바르지 않다")
            out["src"], out["src_tag"] = p["src"], p["src_tag"]
            if "src_inset" in p:
                out["src_inset"] = _num(p["src_inset"], nonneg=True)
        elif "src_tag" in p or "src_inset" in p:
            raise ValueError("난간 출처가 올바르지 않다")
        why = K.row_params_error("railing", out)
        if why:
            raise ValueError(f"난간이 올바르지 않다({why})")
    elif kind == "roof":
        if (set(p) - {"ridge", "dir", "slopes", "overhangs", "rtype", "layers", "eaves", "axis", "profile", "cw_input"} != {"name", "boundary", "form", "pitch", "overhang", "base_offset", "thickness"}
                or not isinstance(p["boundary"], list)
                or not 3 <= len(p["boundary"]) <= K.RS.PTS_MAX or any(not isinstance(q, list) or len(q) != 2 for q in p["boundary"])
                or ("slopes" in p and (not isinstance(p["slopes"], list) or len(p["slopes"]) != len(p["boundary"])))
                or ("overhangs" in p and (not isinstance(p["overhangs"], list) or len(p["overhangs"]) != len(p["boundary"])))):
            raise ValueError("지붕 params 가 다르다")
        out = {"name": _name(p["name"]), "boundary": [[_num(q[0]), _num(q[1])] for q in p["boundary"]], "form": p["form"], "pitch": _num(p["pitch"], pos=True),
               "overhang": _num(p["overhang"], nonneg=True), "base_offset": _num(p["base_offset"]), "thickness": _num(p["thickness"], pos=True)}
        if "ridge" in p:
            out["ridge"] = p["ridge"]
        if "dir" in p:
            out["dir"] = _num(p["dir"])
        if "slopes" in p:   # 다각형 외곽 지붕(W1 1-F 4차)의 변마다 물매
            out["slopes"] = [_num(v, nonneg=True) for v in p["slopes"]]
        if "overhangs" in p:   # 외곽선 지붕(W2 여덟 번째 PR)의 변마다 내밀기
            out["overhangs"] = [_num(v, nonneg=True) for v in p["overhangs"]]
        if "axis" in p or "profile" in p:   # 압출 지붕(W2 여덟 번째 PR)
            if not isinstance(p.get("axis"), list) or not isinstance(p.get("profile"), list) or any(not isinstance(q, list) or len(q) != 2 for q in p["profile"]):
                raise ValueError("압출 지붕 축·프로파일이 올바르지 않다")
            out["axis"] = [_num(v) for v in p["axis"]]
            out["profile"] = [[_num(q[0]), _num(q[1])] for q in p["profile"]]
        if "rtype" in p:   # 지붕 유형(W2 여덟 번째 PR): 값 검사는 코어 행 검사가 한다(정의가 없으면 아래 _restore_roof_types 가 뗀다)
            out["rtype"] = p["rtype"]
        if "layers" in p:   # 지붕 층 구성(W2 여덟 번째 PR)
            if not isinstance(p["layers"], list) or any(not isinstance(x, dict) or set(x) != {"material", "thickness"} for x in p["layers"]):
                raise ValueError("지붕 층 구성이 올바르지 않다")
            out["layers"] = [{"material": x["material"], "thickness": _num(x["thickness"], pos=True)} for x in p["layers"]]
        if "eaves" in p:   # 처마 부속(W2 여덟 번째 PR): 치수는 float 로 맞추고 변 번호는 정수 그대로
            if not isinstance(p["eaves"], dict) or any(not isinstance(d, dict) for d in p["eaves"].values()):
                raise ValueError("지붕 처마 부속이 올바르지 않다")
            out["eaves"] = {k: {x: (list(v) if x == "edges" else (v if x == "material" else _num(v, pos=True))) for x, v in d.items()} for k, d in p["eaves"].items()}
        if "cw_input" in p:   # 시계 방향으로 받은 외곽(W2 아홉 번째 PR): 값 검사는 코어 행 검사가 한다(True 만)
            out["cw_input"] = p["cw_input"]
        why = K.row_params_error("roof", out)
        if why:
            raise ValueError(f"지붕이 올바르지 않다({why})")
    elif kind == "elevator":
        if (set(p) - set(K_ELEV_OPT) != {"name", "shaft", "cab", "base_offset", "rise"} or not isinstance(p["shaft"], list) or len(p["shaft"]) != 4
                or not isinstance(p["cab"], list) or len(p["cab"]) != 3):
            raise ValueError("승강기 params 가 다르다")
        out = {"name": _name(p["name"]), "shaft": [_num(v) for v in p["shaft"]], "cab": [_num(v) for v in p["cab"]],
               "base_offset": _num(p["base_offset"]), "rise": _num(p["rise"], pos=True)}
        if "pit" in p:   # 선택 입력(피트·기계실·승강장 문·소유 구멍): 값 검사는 코어 행 검사와 같은 것이다
            out["pit"] = _num(p["pit"], pos=True)
        if "machine" in p:
            if not isinstance(p["machine"], dict) or set(p["machine"]) != {"height"}:
                raise ValueError("승강기 기계실이 올바르지 않다")
            out["machine"] = {"height": _num(p["machine"]["height"], pos=True)}
        if "doors" in p:
            dr = p["doors"]
            if not isinstance(dr, dict) or set(dr) != {"face", "w", "h"}:
                raise ValueError("승강기 문이 올바르지 않다")
            out["doors"] = {"face": dr["face"], "w": _num(dr["w"], pos=True), "h": _num(dr["h"], pos=True)}
        for k in ("stops", "hole_slabs"):
            if k in p:
                if not isinstance(p[k], list) or any(not isinstance(x, str) or not _ID.match(x) for x in p[k]):
                    raise ValueError(f"승강기 {k} 가 올바르지 않다")
                out[k] = list(p[k])
        if "hole_clearance" in p:
            out["hole_clearance"] = _num(p["hole_clearance"], nonneg=True)
        why = K.row_params_error("elevator", out)
        if why:
            raise ValueError(f"승강기가 올바르지 않다({why})")
    elif kind == "stair":
        if (set(p) - {"turn"} != {"name", "run", "width", "height", "risers", "base_offset"} or not isinstance(p["run"], list) or len(p["run"]) != 4
                or type(p["risers"]) is not int or not K.STAIR_RISERS_MIN <= p["risers"] <= K.STAIR_RISERS_MAX):
            raise ValueError("계단 params 가 다르다")
        run = [_num(v) for v in p["run"]]
        if (run[2] - run[0]) ** 2 + (run[3] - run[1]) ** 2 < 1.0:
            raise ValueError("계단 걸음선이 1mm 미만이다")
        out = {"name": _name(p["name"]), "run": run, "width": _num(p["width"], pos=True), "height": _num(p["height"], pos=True),
               "risers": p["risers"], "base_offset": _num(p["base_offset"])}
        if "turn" in p:
            t = p["turn"]
            if not isinstance(t, dict) or set(t) - {"winders"} != {"type", "side", "risers_a", "landing", "gap"}:
                raise ValueError("계단 turn 이 올바르지 않다")
            out["turn"] = {"type": t["type"], "side": t["side"], "risers_a": t["risers_a"],
                           "landing": None if t["landing"] is None else _num(t["landing"], pos=True), "gap": _num(t["gap"], nonneg=True)}
            if "winders" in t:   # 돌음 계단(23.3): 정수 검사는 row_params_error 가 한다
                out["turn"]["winders"] = t["winders"]
            why = K.row_params_error("stair", out)
            if why:
                raise ValueError(f"계단 turn 이 올바르지 않다({why})")
    else:
        if set(p) - {"leaf"} != {"name", "width", "height", "sill", "offset"}:
            raise ValueError("문 params 가 다르다")
        out = {"name": _name(p["name"]), "width": _num(p["width"], pos=True), "height": _num(p["height"], pos=True),
               "sill": _num(p["sill"], nonneg=True), "offset": _num(p["offset"])}
        if "leaf" in p:
            out["leaf"] = _num(p["leaf"], pos=True)
    res = {"kind": kind, "params": out}
    if kind == "wall":
        lv, joins = d["level"], d["joins"]
        if not isinstance(lv, str) or not _ID.match(lv) or not isinstance(joins, list) or not all(isinstance(j, str) and _ID.match(j) for j in joins):
            raise ValueError("레벨·조인 참조가 올바르지 않다")
        res["level"], res["joins"] = lv, sorted(set(joins))
    elif kind in ("slab", "stair", "space", "railing", "elevator", "roof", "solid", "curtwall", "stackwall"):
        if not isinstance(d["level"], str) or not _ID.match(d["level"]):
            raise ValueError("레벨 참조가 올바르지 않다")
        res["level"] = d["level"]
    elif kind == "door":
        if not isinstance(d["host"], str) or not _ID.match(d["host"]):
            raise ValueError("호스트 참조가 올바르지 않다")
        res["host"] = d["host"]
    return res


def _restore_wall_types(rows: dict, rels: set, plan: "ImportPlan") -> None:
    """벽 유형(W2 일곱 번째 PR): 저작 벽의 params.wtype 이 되살린 벽 유형 정의를 가리키면 타입 관계(of_type)를 잇고, 아니면 유형만 뗀다
    (층 구성·두께는 벽에 남는다: 정의가 손상돼도 벽 형상은 그대로다)."""
    for g in sorted(rows):
        r = rows[g]
        tid = r["params"].get("wtype") if r["kind"] == "wall" and isinstance(r.get("params"), dict) else None
        if tid is None:
            continue
        if rows.get(tid, {}).get("kind") == "walltype":   # 분리된 벽도 관계를 잇는다(params 와 관계가 같은 뜻이어야 한다)
            rels.add(("of_type", g, tid))
        else:
            r["params"] = {k: v for k, v in r["params"].items() if k != "wtype"}
            r.pop("spec_hash", None)
            plan.warnings.append(f"{g}: 벽 유형 {str(tid)[:22]} 정의를 되살리지 못해 벽에서 유형을 뗐다(층 구성은 남는다)")


def _restore_roof_types(rows: dict, rels: set, plan: "ImportPlan") -> None:
    """지붕 유형(W2 여덟 번째 PR): 저작 지붕의 params.rtype 이 되살린 지붕 유형 정의를 가리키면 타입 관계(of_type)를 잇고, 아니면 유형만 뗀다
    (층 구성·두께는 지붕에 남는다)."""
    for g in sorted(rows):
        r = rows[g]
        tid = r["params"].get("rtype") if r["kind"] == "roof" and isinstance(r.get("params"), dict) else None
        if tid is None:
            continue
        if rows.get(tid, {}).get("kind") == "rooftype":
            rels.add(("of_type", g, tid))
        else:
            r["params"] = {k: v for k, v in r["params"].items() if k != "rtype"}
            r.pop("spec_hash", None)
            plan.warnings.append(f"{g}: 지붕 유형 {str(tid)[:22]} 정의를 되살리지 못해 지붕에서 유형을 뗐다(층 구성은 남는다)")


def _finst_dependency_error(g: str, it: dict, cand: dict, cls_of: dict) -> str | None:
    """패밀리 인스턴스 후보가 가리키는 타입·호스트·레벨이 후보로 있고, 레시피로 평가해 의도가 일관된지 확인한다(없거나 어긋나면 이유 문장)."""
    t = cand.get(it["type"])
    if t is None or t["kind"] != "ftype":
        return "패밀리 타입이 없다"
    host = cand.get(it["host"]) if "host" in it else None
    if "host" in it:
        if host is None or host["kind"] != "wall":
            return "호스트 벽이 없다"
    elif cand.get(it["level"], {}).get("kind") != "level":
        return "레벨 참조가 없다"
    try:
        cls = FR.check_finst(it, {"params": t["params"], "family_id": t["family_id"]}, host["params"] if host else None)
    except (ValueError, TypeError, KeyError, ArithmeticError, RecursionError) as ex:
        return f"패밀리 값이 일관되지 않다({str(ex)[:60]})"
    if cls_of[g] != cls:
        return f"IFC 클래스({cls_of[g]})가 레시피의 클래스({cls})와 다르다"
    return None


class _Inputs:
    """계획 단계의 그래프 입력(요소 행과 관계만 있으면 투영 명세를 계산할 수 있다)."""

    def __init__(self, st):
        self.st = st

    def load(self, key):
        st, t = self.st, key[0]
        if t == "el":
            return st.el.get(key[1])
        if t == "rel_out":
            return tuple(st.out.get((key[1], key[2]), ()))
        if t == "rel_in":
            return tuple(st.inn.get((key[1], key[2]), ()))
        if t == "xcut_finsts":   # 함께 자르는 벽이 있는 인스턴스(25단계): 지금 계획에 올라온 행에서 찾는다
            return tuple(sorted(f for f, r in st.el.items() if r is not None and r["kind"] == "finst" and "also_cuts" in r["params"]))
        if t == "arc_walls":   # 곡선(원호) 벽(W2 ARC-04)
            return frozenset(w for w, r in st.el.items() if r is not None and r["kind"] == "wall" and "arc" in r["params"])
        if t == "count":   # 독립 개구 객체(W2 세 번째 PR, ARC-34): 호스트 명세가 개구 객체를 센다
            return st.count.get(key[1], 0)
        if t == "bucket":
            return tuple(st.bucket.get((key[1], key[2]), ()))
        raise KeyError(key)


def _plan_openings(f, st, rows: dict) -> None:
    """독립 개구 객체(W2 세 번째 PR, ARC-34)의 정의를 계획 상태에 미리 올린다: 호스트(슬래브·벽)의 투영 명세가 개구 부속을 담으므로
    정의 없이 계산하면 들어온 형상과 달라 분리된다. 정의를 되살릴지는 뒤의 _restore_defs 가 정한다(경고는 거기서 한 번만 낸다)."""
    import types
    defs = _read_defs(f, types.SimpleNamespace(warnings=[]))
    for eid in sorted(defs):
        if eid in rows or not isinstance(defs[eid], str):
            continue
        try:
            d = VD.parse_def(eid, defs[eid])
        except ValueError:
            continue
        if d["kind"] == "opening":
            st.apply_el(eid, None, {"kind": "opening", "ifc_class": "", "origin": "authored", "family_id": d["family_id"], "params": d["params"]})


def _read_defs(f, plan: "ImportPlan") -> dict[str, object]:
    """프로젝트 속성셋 DoBIM_Defs 의 속성 {이름: 텍스트(문자열이 아니면 None)}. 프로젝트가 하나가 아니거나 속성셋이 둘 이상이면 모호해서 읽지 않는다.
    같은 이름의 속성이 둘이면 어느 쪽도 믿지 않는다(그 이름은 빠진다)."""
    projs = f.by_type("IfcProject")
    if len(projs) != 1:
        return {}
    psets = []
    for r in projs[0].IsDefinedBy or ():
        d = r.RelatingPropertyDefinition if r.is_a("IfcRelDefinesByProperties") else None
        if isinstance(d, ifcopenshell.entity_instance) and d.is_a("IfcPropertySet") and d.Name == VD.DEFS_PSET:
            psets.append(d)
    if not psets:
        return {}
    if len(psets) > 1:
        plan.warnings.append(f"속성셋 {VD.DEFS_PSET} 가 {len(psets)}개라 뷰·주석 정의를 읽지 않는다")
        return {}
    out: dict[str, object] = {}
    dup: set[str] = set()
    for p in psets[0].HasProperties or ():
        name = getattr(p, "Name", None)
        if not isinstance(name, str):
            continue
        if name in out or name in dup:
            dup.add(name)
            out.pop(name, None)
            continue
        if len(out) >= VD.MAX_DEFS:
            plan.warnings.append(f"뷰·주석 정의가 {VD.MAX_DEFS}개를 넘어 나머지는 읽지 않는다")
            break
        v = getattr(p.NominalValue, "wrappedValue", None) if p.is_a("IfcPropertySingleValue") and p.NominalValue is not None else None
        out[name] = v if isinstance(v, str) else None
    for name in sorted(dup)[:20]:
        plan.warnings.append(f"{name[:22]}: 같은 이름의 정의 속성이 둘 이상이라 되살리지 않는다")
    return out


def _restore_defs(f, rows: dict, rels: set, taken: set, plan: "ImportPlan") -> None:
    """뷰·치수·태그를 되살려 rows·rels 에 더한다(ADR-0015). rows 는 요소 판정이 끝난 상태(외래 행 포함), taken 은 파일 안의 모든 id(제품·타입·뼈대)다.
    되살리지 못한 정의는 이유를 경고로 알리고 건너뛴다(오류로 죽지 않는다)."""
    defs = _read_defs(f, plan)
    if not defs:
        return
    skip = lambda eid, why: plan.warnings.append(f"{eid[:22]}: 뷰·주석 정의를 되살리지 않는다({why})")   # noqa: E731
    parsed: dict[str, dict] = {}
    for eid in sorted(defs):
        if eid in taken or eid in rows:
            skip(eid, "정의 id 가 요소의 GlobalId 와 겹친다")
            continue
        try:
            parsed[eid] = VD.parse_def(eid, defs[eid])
        except ValueError as ex:
            skip(eid, str(ex)[:100])
    names: dict[str, list] = {}
    for eid, d in parsed.items():
        if d["kind"] == "view":
            names.setdefault(d["params"]["name"], []).append(eid)
    for nm, ids in sorted(names.items()):
        if len(ids) > 1:   # 뷰 이름은 유일해야 한다(view.create 의 규칙): 겹치면 어느 쪽이 원본인지 알 수 없다
            for eid in ids:
                del parsed[eid]
                skip(eid, "같은 이름의 뷰가 더 있다")

    def state(g):   # (종류, 출처) 또는 None(파일에 없다)
        r = rows.get(g)
        if r is not None:
            return r["kind"], r["origin"]
        if g in taken:
            return ("?", "foreign")
        if g in defs:   # 같은 파일의 정의 id(되살려지든 아니든): 존재하지만 요소 종류가 아니다. 고아(없음)로 보면 종류 검사를 건너뛰어 위조 참조가 통과한다
            return ("def", "def")
        return None

    on_level = {g: lv for k, g, lv in rels if k == "on_level"}      # 벽 -> 레벨
    host_of = {d: w for k, w, d in rels if k == "hosts"}            # 문 -> 호스트 벽

    def level_of(kind, eid):   # 주석 대상이 놓인 레벨(저작 요소만 묻는다)
        if kind == "level":
            return eid
        return on_level.get(host_of.get(eid, eid) if kind in ("door", "finst") else eid)   # 자립 패밀리 인스턴스는 레벨에 직접 선다

    ok: dict[str, dict] = {}
    tnames: dict[str, list] = {}
    for eid in sorted(e for e, d in parsed.items() if d["kind"] == "vtemplate"):   # 뷰 템플릿(W1 1-C): 참조가 없다. 이름이 겹치면 어느 쪽이 원본인지 알 수 없다
        tnames.setdefault(parsed[eid]["params"]["name"], []).append(eid)
    for nm, ids in sorted(tnames.items()):
        for eid in ids:
            if len(ids) > 1:
                skip(eid, "같은 이름의 뷰 템플릿이 더 있다")
            else:
                ok[eid] = parsed[eid]
    for eid in sorted(e for e, d in parsed.items() if d["kind"] == "view"):
        p = parsed[eid]["params"]
        if "template" in p and ok.get(p["template"], {}).get("kind") != "vtemplate":   # 되살린 템플릿이 아니면(없음·다른 종류) 뷰는 템플릿 없이 되살린다
            plan.warnings.append(f"{eid[:22]}: 뷰가 따르는 뷰 템플릿을 되살리지 못해 템플릿 없이 되살린다")
            p = parsed[eid]["params"] = {k: v for k, v in p.items() if k != "template"}
        if p["type"] == "plan":
            st = state(p["level"])
            fl = st is not None and st[0] == "foreign" and Q.plan_level_ok(rows.get(p["level"]))   # 외래 IFC 층(1단계 외래 도면 경로): 고도를 IFC 배치에서 읽어 낡은 값이 없다
            if st is not None and not fl and (K.ref_kind_error("view", "level", st[0]) or st[1] != "authored"):   # 레벨이 없으면 고아 뷰(저작 상태에서도 생긴다), 있는데 저작 레벨이 아니면(분리·외래·다른 종류·정의 id) 낡은 값을 쓴다
                skip(eid, "평면 뷰가 가리키는 레벨이 저작이 아니다(분리·외래·다른 종류)")
                continue
        ok[eid] = parsed[eid]
    for eid in sorted(e for e, d in ok.items() if d["kind"] == "view" and "parent" in d["params"]):   # 콜아웃 뷰(W1 1-C 2차): 부모 뷰를 되살렸을 때만 콜아웃으로 남긴다
        par = ok[eid]["params"]["parent"]
        if par == eid or ok.get(par, {}).get("kind") != "view" or "parent" in ok[par]["params"] or ok[par]["params"]["type"] != ok[eid]["params"]["type"]:
            plan.warnings.append(f"{eid[:22]}: 콜아웃 뷰의 부모 뷰를 되살리지 못해 잘라낸 일반 뷰로 되살린다")
            ok[eid]["params"] = {k: v for k, v in ok[eid]["params"].items() if k != "parent"}
    gnames: dict[str, list] = {}
    for eid in sorted(e for e, d in parsed.items() if d["kind"] == "grid"):   # 통심선(19단계): 참조가 없다. 기호 문자가 겹치면 어느 쪽이 원본인지 알 수 없다
        gnames.setdefault(parsed[eid]["params"]["name"], []).append(eid)
    for nm, ids in sorted(gnames.items()):
        for eid in ids:
            if len(ids) > 1:
                skip(eid, "같은 기호의 통심선이 더 있다")
            else:
                ok[eid] = parsed[eid]
    for eid in sorted(e for e, d in parsed.items() if d["kind"] in ("dim", "tag")):
        d = parsed[eid]
        p = d["params"]
        v = ok.get(p["view"])
        if v is None:
            skip(eid, "속한 뷰가 없거나 되살려지지 않았다")
            continue
        vt = v["params"]["type"]
        why = None
        if d["kind"] == "dim":
            if ("at" in p) == (vt == "plan") or vt in ("legend", "drafting"):   # 모델이 없는 뷰(범례·제도 뷰)에는 모델 치수가 없다
                why = "치수의 종류(평면·높이)가 뷰의 종류와 맞지 않는다"
            for k in ("a", "b"):
                rk, rid, _role = AN.parse_ref(p[k])
                st = state(rid)
                if rk == "grid":   # 통심선(W1 1-B 2차): 정의로 왕복한다. 같은 파일에서 되살린 통심선이어야 하고 레벨이 없다
                    if st is not None and ok.get(rid, {}).get("kind") != "grid":
                        why = f"치수 참조 {k} 의 대상이 되살린 통심선이 아니다"
                elif st is not None and st != (rk, "authored"):
                    why = f"치수 참조 {k} 의 대상이 저작 {rk} 가 아니다"
                elif st is not None and vt == "plan" and level_of(rk, rid) != v["params"]["level"]:
                    why = f"치수 참조 {k} 의 대상이 이 평면 뷰의 레벨에 있지 않다"
        else:
            st = state(p["target"])
            if vt != "plan":
                why = "태그는 평면 뷰에 둔다"
            elif st is not None and (K.ref_kind_error("tag", "target", st[0]) or st[1] != "authored" or not VD.tag_fields_ok(st[0], p["fields"])):
                why = "태그 대상이 태그할 수 있는 저작 요소가 아니거나 표시 필드가 맞지 않는다"
            elif st is not None and level_of(st[0], p["target"]) != v["params"]["level"]:
                why = "태그 대상이 이 평면 뷰의 레벨에 있지 않다"
        if why:
            skip(eid, why)
            continue
        ok[eid] = d
    # 시트 정의: 올린 뷰가 모두 되살려진 뷰여야 하고(하나라도 없으면 시트 전체를 되살리지 않는다: 일부만 남기면 다른 도면이 된다), 이름이 겹치지 않는다
    snames: dict[str, list] = {}
    for eid in sorted(e for e, d in parsed.items() if d["kind"] == "sheet"):
        snames.setdefault(parsed[eid]["params"]["name"], []).append(eid)
    for nm, ids in sorted(snames.items()):
        for eid in ids:
            p = parsed[eid]["params"]
            if len(ids) > 1:
                skip(eid, "같은 이름의 시트가 더 있다")
            elif any(ok.get(v) is None or ok[v]["kind"] != "view" for v in p["views"]):
                skip(eid, "시트가 올린 뷰가 없거나 되살려지지 않았다")
            elif p["key_plan"] is not None and (ok.get(p["key_plan"]) is None or ok[p["key_plan"]]["kind"] != "view" or ok[p["key_plan"]]["params"]["type"] != "plan"):
                skip(eid, "키 플랜이 평면 뷰가 아니거나 되살려지지 않았다")
            else:
                ok[eid] = parsed[eid]
    # 2D 작도(27단계): 층 스타일은 이름이 겹치면 어느 쪽이 원본인지 알 수 없다. 2D 요소는 속한 뷰가 되살려진 뷰여야 한다(참조는 뷰 하나뿐)
    lnames: dict[str, list] = {}
    for eid in sorted(e for e, d in parsed.items() if d["kind"] == "dlayer"):
        lnames.setdefault(parsed[eid]["params"]["name"], []).append(eid)
    for nm, ids in sorted(lnames.items()):
        for eid in ids:
            if len(ids) > 1:
                skip(eid, "같은 이름의 2D 층 스타일이 더 있다")
            else:
                ok[eid] = parsed[eid]
    for eid in sorted(e for e, d in parsed.items() if d["kind"] == "draft"):
        v = ok.get(parsed[eid]["params"]["view"])
        if v is None or v["kind"] != "view":
            skip(eid, "2D 요소가 속한 뷰가 없거나 되살려지지 않았다")
        else:
            ok[eid] = parsed[eid]
    # 리비전·키노트(W1 1-B 3차): 표기·순번·키가 겹치면 어느 쪽이 원본인지 알 수 없다. 리비전이 싣는 시트는 되살린 시트이거나 파일에 없는 id(지운 시트)여야 한다
    for kind, keys in (("keynote", ("name",)), ("revision", ("name", "seq"))):
        seen: dict = {}
        for eid in sorted(e for e, d in parsed.items() if d["kind"] == kind):
            for k in keys:
                seen.setdefault((k, parsed[eid]["params"][k]), []).append(eid)
        dup = {e for ids in seen.values() if len(ids) > 1 for e in ids}
        for eid in sorted(e for e, d in parsed.items() if d["kind"] == kind):
            if eid in dup:
                skip(eid, "같은 키의 키노트가 더 있다" if kind == "keynote" else "같은 표기·순번의 리비전이 더 있다")
            elif kind == "revision" and any(state(x) is not None and ok.get(x, {}).get("kind") != "sheet" for x in parsed[eid]["params"]["sheets"]):
                skip(eid, "리비전이 싣는 시트가 되살린 시트가 아니다")
            else:
                ok[eid] = parsed[eid]
    # 프로젝트 매개변수(W1 연장, DATA-03): 속성셋.속성이 겹치면 어느 쪽이 원본인지 알 수 없다
    pkeys: dict = {}
    for eid in sorted(e for e, d in parsed.items() if d["kind"] == "pparam"):
        pkeys.setdefault((parsed[eid]["params"]["pset"], parsed[eid]["params"]["name"]), []).append(eid)
    for ids in pkeys.values():
        for eid in ids:
            if len(ids) > 1:
                skip(eid, "같은 속성셋.속성의 프로젝트 매개변수가 더 있다")
            else:
                ok[eid] = parsed[eid]
    # 재료 외관 정의(W2 다섯 번째 PR, DATA-05): 재료 이름이 겹치면 어느 쪽이 원본인지 알 수 없다
    mkeys: dict = {}
    for eid in sorted(e for e, d in parsed.items() if d["kind"] == "matdef"):
        mkeys.setdefault(parsed[eid]["params"]["name"], []).append(eid)
    for ids in mkeys.values():
        for eid in ids:
            if len(ids) > 1:
                skip(eid, "같은 재료 이름의 외관 정의가 더 있다")
            else:
                ok[eid] = parsed[eid]
    # 벽 유형(W2 일곱 번째 PR, ARC-11)·지붕 유형(W2 여덟 번째 PR): 유형 이름은 유일하다(walltype·rooftype.create 의 규칙). 겹치면 어느 쪽이 원본인지 알 수 없다
    for tk, what in (("walltype", "벽 유형"), ("rooftype", "지붕 유형")):
        tkeys: dict = {}
        for eid in sorted(e for e, d in parsed.items() if d["kind"] == tk):
            tkeys.setdefault(parsed[eid]["params"]["name"], []).append(eid)
        for ids in tkeys.values():
            for eid in ids:
                if len(ids) > 1:
                    skip(eid, f"같은 이름의 {what}이 더 있다")
                else:
                    ok[eid] = parsed[eid]
    # 법정 면적 객체(W1 연장, ARC-36): 바닥면적 대상은 가리키는 레벨이 저작 레벨이어야 한다(평면 뷰와 같은 규칙). 레벨이 파일에 없으면 고아로 되살린다
    for eid in sorted(e for e, d in parsed.items() if d["kind"] == "larea"):
        lv = parsed[eid]["params"].get("level")
        st = None if lv is None else state(lv)
        if st is not None and (K.ref_kind_error("larea", "level", st[0]) or st[1] != "authored"):
            skip(eid, "법정 면적 객체가 가리키는 레벨이 저작이 아니다(분리·외래·다른 종류)")
        else:
            ok[eid] = parsed[eid]
    # 독립 개구 객체(W2 세 번째 PR, ARC-34): 가리키는 호스트(슬래브·벽)·레벨이 파일에 있으면 그 종류의 저작 요소여야 한다(없으면 고아로 되살린다)
    for eid in sorted(e for e, d in parsed.items() if d["kind"] == "opening"):
        p = parsed[eid]["params"]
        why = None
        for k, need in (("host", "wall" if p["mode"] == "wall" else "slab"), ("level", "level")):
            st = state(p[k]) if k in p else None
            if st is not None and (st[0] != need or st[1] != "authored"):
                why = f"독립 개구가 가리키는 {k} 가 저작 {need} 가 아니다"
        if why:
            skip(eid, why)
        else:
            ok[eid] = parsed[eid]
    for eid, d in ok.items():
        rows[eid] = {"kind": d["kind"], "ifc_class": "", "origin": "authored", "family_id": d["family_id"], "params": d["params"],
                     "projection_hash": None}
        if d["kind"] in ("dim", "tag", "draft"):
            rels.add(("annot_of", eid, d["params"]["view"]))


def _name_of(p) -> str:
    n = getattr(p, "Name", None)
    return (n if isinstance(n, str) else "")[:NAME_MAX]


# ---------------------------------------------------------------- 계획
def plan_import(data: bytes) -> ImportPlan:
    from dobim_core.engine import State, digest   # 지연 import: 엔진이 이 모듈을 가져온다

    if len(data) > MAX_IMPORT_BYTES:
        raise DobimError("IMPORT_INVALID", f"파일이 너무 크다(상한 {MAX_IMPORT_BYTES // 2**20}MB)", path="/source")
    try:
        f = open_ifc_bytes(data)
    except Exception as ex:
        raise DobimError("IMPORT_INVALID", f"IFC 를 읽을 수 없다({type(ex).__name__})", path="/source") from None
    schema = f.schema
    if schema != "IFC4":
        raise DobimError("UNSUPPORTED_SCHEMA", f"IFC4 만 가져올 수 있다(이 파일: {schema})", path="/source",
                         hint="IFC2x3·IFC4X3 은 S2 범위 밖이다: IFC4 로 다시 내보내세요")
    plan = ImportPlan(schema=schema)
    sk, sk_warning = skeleton_check(f)
    if sk_warning:
        plan.warnings.append(sk_warning)
    infra = {sk[0].GlobalId, sk[1].GlobalId} if sk else set()
    plan.skeleton = bool(sk)
    prods = [p for p in f.by_type("IfcProduct") if p.GlobalId not in infra]
    seen: set[str] = set()
    for p in prods:
        g = p.GlobalId
        if not isinstance(g, str) or not _ID.match(g):
            raise DobimError("IMPORT_INVALID", "GlobalId 가 22자 형식이 아닌 요소가 있다", path="/source",
                             hint="요소를 id 로 가리킬 수 없다: 22자 GlobalId 로 다시 내보내세요")
        if g in seen:
            raise DobimError("IMPORT_INVALID", f"GlobalId 가 겹치는 요소가 있다: {g}", path="/source")
        seen.add(g)
    types = [t for t in f.by_type("IfcTypeObject") if isinstance(getattr(t, "GlobalId", None), str)]
    for t in types:   # 타입 객체도 같은 id 공간을 쓴다(요소 id 로 가리킨다): 제품이나 다른 타입과 겹치면 id 로 가리킬 수 없다
        g = t.GlobalId
        if g in seen:
            raise DobimError("IMPORT_INVALID", f"GlobalId 가 겹치는 요소가 있다: {g}", path="/source")
        seen.add(g)

    # 1) 의도 복원 후보. 문·벽·레벨은 IFC 클래스로 종류를 정하고, 패밀리(ftype/finst)는 의도가 스스로 밝힌 종류를 따른다(인스턴스도 IfcDoor 일 수 있다)
    cand: dict[str, dict] = {}
    cls_of: dict[str, str] = {}   # 패밀리 후보 GlobalId -> 파일 안의 실제 IFC 클래스(레시피의 클래스와 일치하는지 검사한다)
    for p in [*prods, *types]:
        is_type = p.is_a("IfcTypeObject")
        pset = ue.get_pset(p, INTENT_PSET, should_inherit=False)
        text = pset.get("Intent") if pset else None
        if not isinstance(text, str):
            continue
        claimed = FR.claimed_kind(text)
        kind = claimed if claimed in ("ftype", "finst") else KIND_OF.get(p.is_a())
        if claimed == "solid":   # 일반 솔리드는 클래스가 범주마다 다르다: 의도가 밝힌 종류를 따르고, 클래스는 아래에서 범주와 맞춰 본다
            kind = "solid"
        if claimed == "stackwall" and p.is_a() == "IfcWall":   # 스택 벽(W2 일곱 번째 PR): 같은 IfcWall 클래스라 의도가 밝힌 종류를 따른다
            kind = "stackwall"
        if is_type and kind != "ftype":
            continue   # 타입 객체는 패밀리 타입 의도만 본다(그 밖의 타입은 요소가 아니라 파일에 그대로 남는다)
        if kind is None:
            continue
        try:
            if kind in ("ftype", "finst"):
                if not _ID.match(p.GlobalId):
                    raise ValueError("GlobalId 가 22자 형식이 아니다")
                d = FR.load_intent(text)
                cand[p.GlobalId] = FR.parse_ftype(d) if kind == "ftype" else FR.parse_finst(d)
                cls_of[p.GlobalId] = p.is_a()
            else:
                cand[p.GlobalId] = _parse_intent(kind, text)
                if kind == "solid":
                    cls_of[p.GlobalId] = p.is_a()
        except (ValueError, TypeError, KeyError, ArithmeticError, RecursionError) as ex:   # 넘침(OverflowError)도 해석 실패다
            plan.warnings.append(f"{p.GlobalId}: DoBIM_Intent 를 해석할 수 없어 외래로 둔다({str(ex)[:80]})")
    # 2) 가리키는 대상이 있는 것만 남긴다(고정점)
    changed = True
    while changed:
        changed = False
        for g, it in list(cand.items()):
            bad = None
            if it["kind"] == "wall":
                if g in it["joins"] or cand.get(it["level"], {}).get("kind") != "level":
                    bad = "레벨 참조가 없다"
                elif any(cand.get(j, {}).get("kind") != "wall" or g not in cand[j]["joins"] for j in it["joins"]):
                    bad = "조인 상대가 없거나 대칭이 아니다"
                elif any(cand[j]["level"] != it["level"] for j in it["joins"]):
                    bad = "다른 레벨의 벽과 조인돼 있다"
                elif "stack" in it["params"] and (cand.get(it["params"]["stack"], {}).get("kind") != "stackwall"
                                                  or g not in cand[it["params"]["stack"]]["params"]["parts"]):   # 스택 벽 부분 벽(W2 일곱 번째 PR)
                    bad = "스택 벽(전체)이 없거나 이 벽을 부분으로 갖지 않는다"
            elif it["kind"] == "stackwall":   # 스택 벽(W2 일곱 번째 PR): 레벨과 부분 벽이 모두 후보여야 한다
                if cand.get(it["level"], {}).get("kind") != "level":
                    bad = "레벨 참조가 없다"
                elif any(cand.get(w, {}).get("kind") != "wall" or cand[w]["params"].get("stack") != g or cand[w]["level"] != it["level"] for w in it["params"]["parts"]):
                    bad = "부분 벽이 없거나 이 스택 벽에 속하지 않는다"
            elif it["kind"] in ("slab", "stair", "space", "railing", "elevator", "roof", "solid", "curtwall"):
                if cand.get(it["level"], {}).get("kind") != "level":
                    bad = "레벨 참조가 없다"
                elif it["kind"] == "solid" and cls_of.get(g) != K.SO.SOLID_CATEGORIES[it["params"]["category"]][0]:
                    bad = f"IFC 클래스({cls_of.get(g)})가 범주({it['params']['category']})의 클래스와 다르다"
            elif it["kind"] == "door":
                if cand.get(it["host"], {}).get("kind") != "wall":
                    bad = "호스트 벽이 없다"
            elif it["kind"] == "ftype":
                if cls_of[g] != it["ifc_class"]:
                    bad = f"IFC 클래스({cls_of[g]})가 레시피의 타입 클래스({it['ifc_class']})와 다르다"
            elif it["kind"] == "finst":
                bad = _finst_dependency_error(g, it, cand, cls_of)
            if bad:
                del cand[g]
                plan.warnings.append(f"{g}: 의도의 {bad} → 외래로 둔다")
                changed = True

    # 3) 후보를 저작 행으로 만들고 투영 명세를 계산한다
    rows: dict[str, dict] = {}
    rels: set[tuple] = set()
    for g, it in cand.items():
        if it["kind"] in ("ftype", "finst"):
            rows[g] = {"kind": it["kind"], "ifc_class": cls_of[g], "origin": "authored", "family_id": it["family_id"], "params": it["params"]}
        else:
            cls = K.SO.SOLID_CATEGORIES[it["params"]["category"]][0] if it["kind"] == "solid" else {"level": "IfcBuildingStorey", "wall": "IfcWall", "door": "IfcDoor", "slab": "IfcSlab", "stair": "IfcStair", "space": "IfcSpace", "railing": "IfcRailing", "elevator": "IfcTransportElement", "roof": "IfcRoof", "site": "IfcSite", "curtwall": "IfcCurtainWall", "stackwall": "IfcWall"}[it["kind"]]
            rows[g] = {"kind": it["kind"], "ifc_class": cls, "origin": "authored", "family_id": FAMILY[it["kind"]], "params": it["params"]}
        if it["kind"] == "wall":
            rels.add(("on_level", g, it["level"]))
            rels.update(("joins", min(g, j), max(g, j)) for j in it["joins"])
        elif it["kind"] in ("slab", "stair", "space", "railing", "elevator", "roof", "solid", "curtwall", "stackwall"):
            rels.add(("on_level", g, it["level"]))
        elif it["kind"] == "door":
            rels.add(("hosts", it["host"], g))
        elif it["kind"] == "finst":
            rels.add(("of_type", g, it["type"]))
            rels.add(("hosts", it["host"], g) if "host" in it else ("on_level", g, it["level"]))
    st = State()
    for g, r in rows.items():
        st.apply_el(g, None, r)
    for k, s_, d_ in rels:
        st.apply_rel(k, s_, d_, True)
    _plan_openings(f, st, rows)
    graph = Graph(_Inputs(st))

    scratch = IfcModel(str(uuid.UUID(int=0)))
    projected: set[str] = set()
    prev_kind = None
    for g in sorted(rows, key=lambda g: (ELEMENT_KINDS[rows[g]["kind"]].order, g)):
        if rows[g]["kind"] != prev_kind:   # 종류가 바뀌면 모은 관계를 반영한다(문의 개구부는 벽이 담긴 층을 따라가는 등 아래 종류가 윗 종류의 관계를 읽는다)
            scratch.flush_rels()
            prev_kind = rows[g]["kind"]
        # 호스트가 없는 패밀리 인스턴스(자립형)만 일괄로 담는다: 같은 층·타입에 N개면 관계에 하나씩 더하는 것이 이차 시간이다.
        # 문과 호스팅 인스턴스는 개구부를 채울 때(add_filling) 담긴 층을 읽으므로 바로 반영한다
        try:
            spec = graph.q(Q.ifc_spec, g)
            scratch.defer_rels = rows[g]["kind"] == "finst" and not spec["host"]
            scratch.create_identity(g, rows[g]["kind"], rows[g]["ifc_class"])
            scratch.project(g, spec)
            projected.add(g)
        except Exception as ex:   # 레시피가 그 IFC 클래스에 쓸 수 없는 속성을 요구하는 등: 의도의 투영을 만들 수 없으면 분리한다(안전한 쪽)
            rows[g]["origin"] = "detached"
            plan.detached.append({"id": g, "reasons": ["의도를 투영할 수 없다"]})
            plan.warnings.append(f"{g}: 의도를 투영할 수 없다({type(ex).__name__}: {str(ex)[:60]}) → 분리됨(동결)")
    scratch.flush_rels()
    want, have = Closure(scratch.f, scratch.infra), Closure(f, infra)
    for g in rows:
        typed = rows[g]["kind"] == "finst"
        h_hash, h_c = have.digest(f.by_guid(g), typed)
        if g in projected:
            w_hash, w_c = want.digest(scratch.ents[g], typed)
            if w_hash != h_hash:
                reasons = closure_diff(w_c, h_c)
                rows[g]["origin"] = "detached"
                plan.detached.append({"id": g, "reasons": reasons})
                plan.warnings.append(f"{g}: 들어온 형상이 의도의 투영과 다르다({', '.join(reasons[:4])}) → 분리됨(동결)")
        rows[g]["projection_hash"] = h_hash
    # 분리된 패밀리 타입의 인스턴스는 함께 분리한다: 타입이 낡은 정의라 인스턴스를 다시 투영하면 변조된 표현 지도를 가리킨다
    for g in sorted(rows):
        r = rows[g]
        if r["kind"] == "finst" and r["origin"] == "authored" and rows[r["params"]["type"]]["origin"] != "authored":
            r["origin"] = "detached"
            plan.detached.append({"id": g, "reasons": ["패밀리 타입이 분리됨"]})
            plan.warnings.append(f"{g}: 패밀리 타입 {r['params']['type']} 가 분리돼 인스턴스도 분리됨(동결)")
    for g in rows:
        if rows[g]["origin"] == "authored":   # 가져온 뒤 명세가 이 값 그대로면 첫 투영에서 들어온 엔티티를 그대로 둔다(engine.flush_ifc)
            rows[g]["spec_hash"] = digest(graph.q(Q.ifc_spec, g))
    # 길이 단위: 의도의 값은 mm 다. 단위 표기만 바뀐 파일은 요소 클로저가 같아 보이므로 후보 전체를 분리한다
    unit_ok = True
    if rows:
        try:
            scale = uunit.calculate_unit_scale(f)
        except Exception:
            scale = None
        if scale is None or abs(scale - 0.001) > 1e-9:
            unit_ok = False
            n = 0
            for g, r in rows.items():
                if r["origin"] == "authored":
                    r["origin"] = "detached"
                    r.pop("spec_hash", None)
                    plan.detached.append({"id": g, "reasons": ["길이 단위"]})
                    n += 1
            if n:
                plan.warnings.append(f"길이 단위가 밀리미터가 아니다: 의도의 값(mm)을 해석할 수 없어 의도가 있는 요소 {n}개를 모두 분리했다(동결)")

    # 4) 외래 요소
    owned: set[str] = set()   # 저작 문·패밀리 인스턴스의 개구부는 그 요소의 클로저에 속한다: 요소 행을 만들지 않는다
    for g, r in rows.items():
        if r["origin"] == "authored" and r["kind"] in ("door", "finst"):
            for rel in f.get_inverse(f.by_guid(g)):
                if rel.is_a("IfcRelFillsElement") and rel.RelatedBuildingElement == f.by_guid(g):
                    owned.add(rel.RelatingOpeningElement.GlobalId)
        elif r["origin"] == "authored" and r["kind"] in ("slab", "stair", "roof", "wall", "curtwall"):   # 커튼월의 멀리언·패널(W2 ARC-22), 벽의 모양 개구부(18단계), 저작 슬래브의 구멍·파라펫·꺾인 계단의 단 묶음·층참·지붕의 경사면도 그 요소의 클로저에 속한다
            owned.update(p.GlobalId for p in scratch.parts.get(g, {}).values())
    for p in prods:
        g = p.GlobalId
        if g in rows:
            continue
        if g in owned:
            plan.skipped += 1
            continue
        rows[g] = {"kind": "foreign", "ifc_class": p.is_a(), "origin": "foreign", "family_id": None,
                   "params": {"name": _name_of(p)}, "projection_hash": None}
    # 4b) 뷰·치수·태그(정의가 속성셋에 실려 있다, ADR-0015)
    taken = seen | infra
    projs = f.by_type("IfcProject")
    if projs and isinstance(getattr(projs[0], "GlobalId", None), str):
        taken.add(projs[0].GlobalId)
    if unit_ok:
        _restore_defs(f, rows, rels, taken, plan)
    elif _read_defs(f, plan):   # 정의의 값(mm)도 길이 단위가 밀리미터일 때만 해석한다(요소의 의도와 같은 이유)
        plan.warnings.append("길이 단위가 밀리미터가 아니라 뷰·주석 정의를 되살리지 않는다")
    _restore_wall_types(rows, rels, plan)   # 벽 유형(W2 일곱 번째 PR): 정의를 되살린 뒤에 벽의 타입 관계를 잇는다
    _restore_roof_types(rows, rels, plan)   # 지붕 유형(W2 여덟 번째 PR)
    # 5) 공간 소속(on_level): 외래·분리 요소도 층에 담긴 관계를 남긴다(레벨 삭제 검사와 element.query level 필터가 쓴다)
    for p in prods:
        g = p.GlobalId
        if g not in rows or rows[g]["kind"] != "foreign":
            continue   # 저작·분리 벽은 의도의 level 로 이미 on_level 을 가졌고, 문은 호스트 벽을 따라간다
        c = ue.get_container(p, should_get_direct=True)
        if c is not None and c.GlobalId in rows and c.is_a("IfcBuildingStorey"):
            rels.add(("on_level", g, c.GlobalId))
    # 분리된 레벨: 그 위의 저작 요소는 분리되지 않았지만 배치가 레벨 배치를 따르므로 레벨 고도 변경이 절대 위치에 그대로 반영된다
    for lv in [d["id"] for d in plan.detached if rows[d["id"]]["kind"] == "level"]:
        above = [g for k, g, dst in rels if k == "on_level" and dst == lv and rows[g]["origin"] == "authored"]
        if above:
            plan.warnings.append(f"{lv}: 분리된 레벨 위에 저작 요소 {len(above)}개가 있다. 이 요소들은 레벨 배치를 따르므로 "
                                 "레벨의 고도 변경이 절대 위치에 반영된다(의도의 고도는 낡은 값이다)")
    plan.rows = [{"id": g, **r} for g, r in sorted(rows.items())]
    plan.rels = sorted(rels)
    return plan
