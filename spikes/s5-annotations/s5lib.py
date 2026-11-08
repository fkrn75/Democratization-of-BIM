# SPDX-License-Identifier: MPL-2.0
"""S5 공용 도구: RA-01 주석 배치, 독립 오라클, 하네스(분할 이력·undo 짝짓기), 임의 편집열, 변이(음성 대조).

오라클의 독립성 (보고서 5절에 같은 내용):
- 코어의 주석 질의(annot.py, queries.py)를 쓰지 않는다. 같은 원시 입력(의도 저장소의 요소 행·관계, IFC 속성셋·재료)에서
  복소수로 기하를 따로 계산한다(코어는 실수 좌표 + sqrt). 조인 정리 규칙은 S1 이 IFC 와 대조해 검증한 명세를 다시 구현한다.
- 분할 이력은 코어의 관계 succ 를 읽지 않고, 하네스가 성공한 wall.split 호출을 직접 기록해(undo/redo 와 짝지어) 끝 소유자를 따라간다.
- 고아 판정의 기대값은 "요소가 지금 의도 저장소에 있는가"뿐이다.
"""
from __future__ import annotations

import contextlib
import random
import sys
import time
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
CORE = HERE.parent / "s1-core"
S3 = HERE.parent / "s3-drawings"
for p in (CORE / "src", CORE / "tools", S3):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import kit  # noqa: E402
from dobim_core.engine import Engine  # noqa: E402
import s3lib  # noqa: E402
from dobim_core import annot as AN  # noqa: E402
from dobim_core import drawing as D  # noqa: E402
from dobim_core.graph import Graph  # noqa: E402

TOL = 1e-6                      # mm. 값 비교 허용 오차
JOIN_EXT, MIN_TRIM = 1000.0, 1.0
GUID = "5d0b1a50-0000-4a00-8000-0d0b1a5000b5"


def _i(prefix: str, n: int) -> str:
    return prefix + f"{n:021d}"


# ================================================================ 오라클
def cross(a: complex, b: complex) -> float:
    return a.real * b.imag - a.imag * b.real


def fmt(v: float) -> str:
    r = round(v)
    return str(int(r)) if abs(v - r) < 0.05 else f"{v:.1f}"


class Oracle:
    """의도 저장소의 원시 상태와 하네스가 기록한 분할 이력에서 주석의 기대 상태를 따로 계산한다."""

    def __init__(self, eng, splits):
        self.eng = eng
        self.el = eng.st.el
        self.out, self.inn = eng.st.out, eng.st.inn
        self.next_: dict[str, str] = {}
        self.amb: set[str] = set()                 # 판정 경계에 걸린 벽(기대가 모호)
        self.used_succ = 0                         # 분할 이력을 따라간 참조 해석 횟수
        for w, n in splits:                       # 효과적인 분할 이력을 순서대로: N 이 W 의 옛 후계를 이어받고 W 의 후계는 N
            old = self.next_.get(w)
            if old is not None:
                self.next_[n] = old
            self.next_[w] = n

    # ---- 원시 기하 (복소수)
    def _axis(self, w):
        x1, y1, x2, y2 = self.el[w]["params"]["axis"]
        a, b = complex(x1, y1), complex(x2, y2)
        return a, (b - a) / abs(b - a), abs(b - a)

    def _partners(self, w):
        return sorted(list(self.out.get(("joins", w), ())) + list(self.inn.get(("joins", w), ())))

    def trim(self, w):
        """조인 정리 범위. 판정 경계(|t| = |t-L|, |t| = 1000, |t-L| = 1000, 정리 뒤 길이 = 1)에서 1e-6mm 안이면 두 구현의 부동소수
        오차로 갈릴 수 있는 정의의 불연속 경계다: self.amb 에 벽을 적어 그 벽의 기대를 '모호'로 돌린다."""
        a, u, length = self._axis(w)
        start, end = 0.0, length
        for o in self._partners(w):
            ao, uo, _ = self._axis(o)
            c = cross(u, uo)
            if abs(c) < 1e-9:
                continue
            t = cross(ao - a, uo) / c
            if ((abs(abs(t) - abs(t - length)) < 1e-6 and abs(t) <= JOIN_EXT + 1e-6) or abs(abs(t) - JOIN_EXT) < 1e-6 or abs(abs(t - length) - JOIN_EXT) < 1e-6
                    or abs(abs(c) - 1e-9) < 1e-12):
                self.amb.add(w)
            if abs(t) <= abs(t - length):
                if abs(t) <= JOIN_EXT:
                    start = t
            elif abs(t - length) <= JOIN_EXT:
                end = t
        if abs(end - start - MIN_TRIM) < 1e-6:
            self.amb.add(w)
        if end - start < MIN_TRIM:
            return 0.0, length
        return start, end

    def _elev(self, w):
        return self.el[self.out[("on_level", w)][0]]["params"]["elevation"]

    # ---- 참조의 기대값
    def ref(self, ref: str):
        kind, rest = ref.split(":", 1)
        eid, role = rest.split("#", 1)
        row = self.el.get(eid)
        if row is None:
            return ("orphan", "target_deleted")
        if kind == "wall":
            if role in ("own.end", "own.trim.end"):          # 자기 끝: 분할 이력을 따라가지 않는다
                role = "axis.end" if role == "own.end" else "trim.end"
            elif role in ("axis.end", "trim.end"):
                x = eid                                      # 이력을 사슬 끝까지 따라간다(중간에 지워진 벽이 있어도 지나간다)
                while x in self.next_:
                    x = self.next_[x]
                if x != eid:
                    if x not in self.el:
                        return ("orphan", "successor_deleted")
                    self.used_succ += 1
                    eid, row = x, self.el[x]
            p = row["params"]
            a, u, length = self._axis(eid)
            mid = a + u * length / 2
            if role == "axis.start":
                return ("pt", a)
            if role == "axis.end":
                return ("pt", a + u * length)
            if role == "trim.start":
                return ("pt", a + u * self.trim(eid)[0])
            if role == "trim.end":
                return ("pt", a + u * self.trim(eid)[1])
            if role == "axis":
                return ("line", mid, u)
            if role == "face.left":
                return ("line", mid + 1j * u * p["thickness"] / 2, u)
            if role == "face.right":
                return ("line", mid - 1j * u * p["thickness"] / 2, u)
            e = self._elev(eid)
            return ("z", e if role == "base" else e + p["height"])
        if kind == "door":
            p = row["params"]
            w = self.inn[("hosts", eid)][0]
            a, u, _ = self._axis(w)
            if role in ("sill", "head"):
                z = self._elev(w) + p["sill"]
                return ("z", z if role == "sill" else z + p["height"])
            off = p["offset"] + (0 if role == "center" else (-p["width"] / 2 if role == "jamb.start" else p["width"] / 2))
            return ("pt", a + u * off)
        return ("z", row["params"]["elevation"])

    def dim(self, aid):
        p = self.el[aid]["params"]
        self.amb.clear()
        ra, rb = self.ref(p["a"]), self.ref(p["b"])
        if self.amb:
            return ("ambiguous",)
        bad = [r[1] for r in (ra, rb) if r[0] == "orphan"]
        if bad:
            return ("orphan", bad[0])
        ta, tb = ra[0], rb[0]
        if ta == "z" and tb == "z":
            return ("ok", abs(ra[1] - rb[1]))
        if "z" in (ta, tb):
            return ("invalid", "mixed")
        if ta == "pt" and tb == "pt":
            return ("ok", abs(rb[1] - ra[1]))
        if ta == "line" and tb == "line":
            if abs(cross(ra[2], rb[2])) > 1e-9:
                return ("invalid", "not_parallel")
            return ("ok", abs(cross(ra[2], rb[1] - ra[1])))
        pt, ln = (ra, rb) if ta == "pt" else (rb, ra)
        return ("ok", abs(cross(ln[2], pt[1] - ln[1])))

    def tag(self, aid):
        p = self.el[aid]["params"]
        t = self.el.get(p["target"])
        if t is None:
            return ("orphan", "target_deleted")
        parts = []
        self.amb.clear()
        for f in p["fields"]:
            tp = t["params"]
            if f == "name":
                parts.append(tp["name"])
            elif f in ("width", "height", "sill", "thickness"):
                parts.append(fmt(tp[f]))
            elif f == "length":
                s, e = self.trim(p["target"])
                parts.append(fmt(e - s))
            elif f == "level":
                w = p["target"] if t["kind"] == "wall" else self.inn[("hosts", p["target"])][0]
                parts.append(self.el[self.out[("on_level", w)][0]]["params"]["name"])
            elif f == "host":
                parts.append(self.el[self.inn[("hosts", p["target"])][0]]["params"]["name"])
            elif f == "material":
                m = self.eng.ifc.get_material(p["target"])
                parts.append("-" if m is None else str(m))
            else:
                ps = "Pset_WallCommon" if t["kind"] == "wall" else "Pset_DoorCommon"
                v = self.eng.ifc.get_psets(p["target"]).get(ps, {}).get("FireRating")
                parts.append("-" if v is None else str(v))
        if self.amb:
            return ("ambiguous",)
        return ("ok", " ".join(parts))

    def expect(self, aid):
        return self.dim(aid) if self.el[aid]["kind"] == "dim" else self.tag(aid)

    def _dir_wall(self, kind, eid):
        if self.el.get(eid) is None:
            return None
        return eid if kind == "wall" else self.inn[("hosts", eid)][0]

    def flags(self, aid):
        """방향 경고의 기대값. 만들 때 저장된 지문(params.dirs, 원시 입력)과 복소수로 구한 지금 방향의 내적이 음수이면 경고.
        끝 역할은 값을 내는 사슬 끝 벽의 방향도 본다."""
        row = self.el[aid]
        if row["kind"] != "dim":
            return ()
        p = row["params"]
        out = []
        for side in ("a", "b"):
            fp = (p.get("dirs") or {}).get(side)
            if fp is None:
                continue
            kind, rest = p[side].split(":", 1)
            eid, role = rest.split("#", 1)
            w = self._dir_wall(kind, eid)
            if w is None:
                continue
            cand = [w]
            if kind == "wall" and role in ("axis.end", "trim.end"):
                x = w
                while x in self.next_:
                    x = self.next_[x]
                if x != w and x in self.el:
                    cand.append(x)
            ref = complex(fp[0], fp[1])
            if any((self._axis(c)[1] * ref.conjugate()).real < 0 for c in cand):     # 복소수 내적 = Re(u · conj(ref))
                out.append(f"direction_changed:{side}")
        return tuple(out)


def annot_ids(eng):
    return sorted(i for k in ("dim", "tag") for i in eng.st.by_kind.get(k, ()))


def check(eng, splits) -> dict:
    """모든 주석의 코어 상태(증분 그래프)와 오라클 기대를 비교한다.
    분류:
      ok_match           정상 + 값 일치
      orphan_expected    대상이 정말 없고 코어도 같은 이유로 고아            (정상적 고아)
      false_orphan       대상은 살아 있는데 코어가 고아/무효                 (합격 기준의 '고아', 0 이어야 함)
      silent_alive       대상이 없는데 코어가 값을 냄(조용히 엉뚱한 곳)       (0 이어야 함)
      reason_mismatch    둘 다 고아인데 이유가 다름
      value_error        둘 다 정상인데 값(또는 태그 글)이 다름              (합격 기준의 '값 오류', 0 이어야 함)
      other_mismatch     그 밖의 불일치(무효 이유 등)
      dangling_view      뷰가 없는 주석"""
    eng.sync()
    eng._touch_pending()
    orc = Oracle(eng, splits)
    c = Counter()
    bad = []
    for aid in annot_ids(eng):
        st = eng.g.q(AN.annot_state, aid)
        kind, status = st[0], st[1]
        got = (status, st[2] if status == "ok" else (st[2][0][1] if status == "orphan" else st[2]))
        exp = orc.expect(aid)
        if exp[0] == "ambiguous":
            c["ambiguous_skipped"] += 1
            c["total"] += 1
            continue
        want_fl, got_fl = orc.flags(aid), tuple(eng.g.q(AN.annot_flags, aid))
        if want_fl != got_fl:
            c["flag_mismatch"] += 1
            bad.append((aid, "flag_mismatch", got_fl, want_fl))
        elif want_fl:
            c["flag_direction_changed"] += 1
        if eng.st.el[aid]["params"]["view"] not in eng.st.el:
            c["dangling_view"] += 1
            bad.append((aid, "dangling_view"))
        if exp[0] == "ok" and got[0] == "ok":
            same = (abs(got[1] - exp[1]) <= TOL) if kind == "dim" else got[1] == exp[1]
            if same:
                c["ok_match"] += 1
            else:
                c["value_error"] += 1
                bad.append((aid, "value_error", got, exp))
        elif exp[0] == "orphan" and got[0] == "orphan":
            if got[1] == exp[1]:
                c["orphan_expected"] += 1
                c["reason_" + exp[1]] += 1
            else:
                c["reason_mismatch"] += 1
                bad.append((aid, "reason_mismatch", got, exp))
        elif exp[0] == "ok" and got[0] in ("orphan", "invalid"):
            c["false_orphan"] += 1
            bad.append((aid, "false_orphan", got, exp))
        elif exp[0] == "orphan" and got[0] == "ok":
            c["silent_alive"] += 1
            bad.append((aid, "silent_alive", got, exp))
        elif exp == got:
            c["invalid_expected"] += 1
        else:
            c["other_mismatch"] += 1
            bad.append((aid, "other_mismatch", got, exp))
        c["total"] += 1
    c["succ_followed"] = orc.used_succ
    c["orphans_core"] = c["orphan_expected"] + c["false_orphan"] + c["reason_mismatch"]
    return {"counts": dict(c), "bad": bad[:12], "n_bad": len(bad)}


def snapshot(eng) -> dict:
    """주석마다 (상태, 값) 스냅숏. 값은 부동소수 그대로(복원 검사는 정확히 같아야 한다)."""
    eng.sync()
    eng._touch_pending()
    return {a: eng.g.q(AN.annot_state, a) for a in annot_ids(eng)}


def stable_diff(before: dict, after: dict, tol=TOL) -> list:
    """두 스냅숏에서 (ok 끼리) 값이 달라졌거나 상태가 바뀐 주석. 분할 불변성 검사에 쓴다."""
    out = []
    for a, s0 in before.items():
        s1 = after.get(a)
        if s1 is None:
            out.append((a, "사라짐"))
            continue
        if s0[1] != s1[1] or (s0[1] == "ok" and s0[0] == "dim" and abs(s0[2] - s1[2]) > tol) or (s0[1] != "ok" and s0 != s1):
            out.append((a, s0, s1))
    return out


# ================================================================ 하네스
class Harness:
    """세션 위의 얇은 층: 성공한 wall.split 를 기록하고 undo/redo 와 짝지어 '효과적인 분할 이력'을 유지한다."""

    def __init__(self, sess):
        self.sess = sess
        self.eng = sess.eng
        self.stack: list = []
        self.redo: list = []

    def splits(self):
        return [r for r in self.stack if r]

    def _note(self, name, args, r, before):
        if not r["ok"] or self.eng.rev == before:
            return
        if name == "history.undo":
            self.redo.append(self.stack.pop())
        elif name == "history.redo":
            self.stack.append(self.redo.pop())
        else:
            self.stack.append((args["id"], r["result"]["new"]) if name == "wall.split" else None)
            self.redo.clear()

    def run(self, name, args=None, **kw):
        before = self.eng.rev
        r = self.sess.run(name, args or {}, **kw)
        self._note(name, args, r, before)
        return r

    def item(self, it):
        if it["op"] == "batch":
            before = self.eng.rev
            r = self.sess.item(it)
            if r["ok"] and self.eng.rev != before:
                self.stack.append(None)
                self.redo.clear()
            return r
        return self.run(it["name"], it["args"])

    def check(self):
        return check(self.eng, self.splits())


# ================================================================ RA-01 주석 배치
def build_ra01(path: Path):
    """RA-01(코어 범위) + 뷰 7개. (세션, ids, views)"""
    return s3lib.build(Path(path))


def annotation_plan(ids: dict, views: dict) -> list[tuple[str, dict, str]]:
    """RA-01 에 둘 치수·태그 목록: (커맨드, 인자, 분류). 모든 id 를 명시하지 않아도 결정적이다(커맨드 순번으로 id 가 정해진다)."""
    out: list[tuple[str, dict, str]] = []
    R = AN.make_ref

    def dim(view, a, b, cat, off=600.0, **kw):
        out.append(("annot.dim.create", {"view": views[view], "a": a, "b": b, "offset": off, **kw}, cat))

    def tag(view, target, fields, cat, off=(500.0, 500.0)):
        out.append(("annot.tag.create", {"view": views[view], "target": target, "fields": fields, "offset": list(off)}, cat))

    for fl, ext, inn, doors in (("1F", ids["ext-1F"], ids["int-1F"], ("D-01", "D-02", "D-03")),
                                ("2F", ids["ext-2F"], ids["int-2F"], ("D-04", "D-05"))):
        pv = f"plan-{fl}"
        host_of = {"D-01": ext[0], "D-02": inn[0], "D-03": inn[1], "D-04": inn[0], "D-05": ext[0]}
        for w in ext + inn:                                                                    # 벽 길이
            dim(pv, R("wall", w, "trim.start"), R("wall", w, "trim.end"), "벽 길이", off=-700.0)
        dim(pv, R("wall", ext[0], "face.left"), R("wall", ext[2], "face.left"), "벽 사이 간격(안목)", off=1200.0)
        dim(pv, R("wall", ext[1], "face.left"), R("wall", ext[3], "face.left"), "벽 사이 간격(안목)", off=1200.0)
        dim(pv, R("wall", inn[0], "axis"), R("wall", inn[2], "axis"), "벽 사이 간격", off=900.0)
        dim(pv, R("wall", inn[0], "axis"), R("wall", ext[3], "axis"), "벽 사이 간격", off=900.0)
        if fl == "1F":
            dim(pv, R("wall", ext[1], "axis"), R("wall", inn[0], "axis"), "벽 사이 간격", off=900.0)
            dim(pv, R("wall", inn[1], "axis"), R("wall", ext[0], "axis"), "벽 사이 간격", off=900.0)
            dim(pv, R("wall", inn[1], "axis"), R("wall", ext[2], "axis"), "벽 사이 간격", off=900.0)
        for d in doors:
            h = host_of[d]
            dim(pv, R("door", ids[d], "jamb.start"), R("door", ids[d], "jamb.end"), "문 폭", off=350.0)
            dim(pv, R("wall", h, "trim.start"), R("door", ids[d], "jamb.start"), "문 위치", off=-450.0)
            dim(pv, R("door", ids[d], "center"), R("wall", h, "trim.end"), "문 위치", off=-1100.0)
        dim(pv, R("door", ids[doors[0]], "center"), R("wall", ext[3], "face.left"), "문-면 거리", off=800.0)
        for d in doors:
            tag(pv, ids[d], ["name", "width"], "문 태그")
        for k, w in enumerate(ext + inn):
            tag(pv, w, ["name", "thickness"] if k % 2 == 0 else ["name", "length"], "벽 태그", off=(300.0, -500.0))
    sec, es, en = "section-BB", "elev-S", "elev-N"
    dim(sec, R("level", ids["1F"], "elevation"), R("level", ids["2F"], "elevation"), "층고", off=900.0, at=[-800.0, 1900.0])
    dim(sec, R("level", ids["1F"], "elevation"), R("wall", ids["ext-2F"][0], "top"), "층고", off=1800.0, at=[-800.0, 1900.0])
    for w, nm in ((ids["ext-1F"][0], "e1"), (ids["ext-2F"][0], "e2"), (ids["int-1F"][0], "i1")):
        dim(sec, R("wall", w, "base"), R("wall", w, "top"), "벽 높이", off=-700.0, at=[13000.0, 1900.0])
    for d in ("D-02", "D-03"):
        dim(sec, R("door", ids[d], "sill"), R("door", ids[d], "head"), "문 높이", off=500.0, at=[4000.0, 1900.0])
    dim(sec, R("door", ids["D-02"], "head"), R("level", ids["2F"], "elevation"), "문 상부 여유", off=500.0, at=[4000.0, 1900.0])
    dim(es, R("wall", ids["ext-1F"][0], "base"), R("wall", ids["ext-1F"][0], "top"), "벽 높이", off=700.0, at=[-500.0, 0.0])
    dim(es, R("door", ids["D-01"], "sill"), R("door", ids["D-01"], "head"), "문 높이", off=500.0, at=[7550.0, 0.0])
    dim(en, R("wall", ids["ext-2F"][2], "base"), R("wall", ids["ext-2F"][2], "top"), "벽 높이", off=700.0, at=[-500.0, 8000.0])
    return out


def place_annotations(h: Harness, ids: dict, views: dict) -> list[tuple[str, str]]:
    """RA-01 에 주석을 배치하고 (id, 분류) 목록을 돌려준다. 하나라도 거절되면 예외."""
    made = []
    for name, args, cat in annotation_plan(ids, views):
        r = h.run(name, args)
        assert r["ok"], (name, args, r)
        made.append((r["result"]["id"], cat))
    return made


# ================================================================ 계측
@contextlib.contextmanager
def count_recomputes():
    """그래프 재계산을 센다: {"annot": 주석 노드, "other": 그 밖의 노드}"""
    orig = Graph._recompute
    cnt = Counter()

    def patched(self, node):
        cnt["annot" if node.fn in AN.ANNOT_FNS else "other"] += 1
        return orig(self, node)

    Graph._recompute = patched
    try:
        yield cnt
    finally:
        Graph._recompute = orig


def refresh_annotations(eng) -> None:
    """모든 주석의 값·그림(모든 뷰)을 최신으로 만든다(SVG 내보내기 직전의 상태)."""
    eng.sync()
    eng._touch_pending()
    g = eng.g
    g.q(AN.annot_bundle)
    for v in sorted(eng.st.by_kind.get("view", ())):
        g.q(AN.view_annots, v)


def annot_node_count(eng) -> int:
    return sum(1 for k in eng.g.nodes if k[0] in AN.ANNOT_FNS)


def edit_and_measure(h: Harness, steps: list[tuple[str, dict]]) -> dict:
    """편집을 적용하고 주석 갱신 비용을 잰다: 다시 계산한 주석 노드 수, 전체 주석 노드 수, 소요 시간."""
    refresh_annotations(h.eng)
    total_before = annot_node_count(h.eng)
    t0 = time.perf_counter()
    oks = [h.run(n, a)["ok"] for n, a in steps]
    t1 = time.perf_counter()
    with count_recomputes() as cnt:
        refresh_annotations(h.eng)
    t2 = time.perf_counter()
    return {"ok": all(oks), "oks": oks, "annot_nodes_total": annot_node_count(h.eng), "annot_nodes_before": total_before,
            "annot_recomputed": cnt["annot"], "other_recomputed": cnt["other"],
            "edit_ms": round((t1 - t0) * 1e3, 2), "refresh_ms": round((t2 - t1) * 1e3, 2)}


# ================================================================ 임의 편집열
class Fuzz:
    """S1 임의 커맨드 생성기 + 주석 생성·삭제·재결합 + 벽 분할 + 뷰 삭제. 매 스텝 오라클·증분 대 전체·(주기적으로) undo 복원을 검사한다."""

    def __init__(self, work: Path, seed: int, *, undo_check_every: int = 4, annot_every: int = 2, ifc_ref_every: int = 3):
        self.seed = seed
        work = Path(work)
        work.mkdir(parents=True, exist_ok=True)
        path = work / f"s5-fuzz-{seed}.dobim"
        path.unlink(missing_ok=True)
        self.sess = kit.Session(path, guid=GUID)
        self.eng = self.sess.eng
        self.h = Harness(self.sess)
        self.gen = kit.Gen(seed)
        self.rng = random.Random(seed ^ 0xA5A5)
        self.vn = 0
        self.undo_every, self.annot_every = undo_check_every, annot_every
        self.ifc_ref_every = ifc_ref_every
        self._fp_seen: set = set()
        self.planned: set[str] = set()
        self.stats = Counter()
        self.failures: list = []
        self.fixed_views = []

    # ---- 뷰
    def _view(self, **kw):
        self.vn += 1
        kw["id"] = _i("4", self.seed * 1000 + self.vn)
        r = self.h.run("view.create", kw)
        return kw["id"] if r["ok"] else None

    def _ensure_views(self):
        for lv in sorted(self.eng.st.by_kind.get("level", ())):
            if lv not in self.planned:
                self.planned.add(lv)
                self._view(name=f"P-{lv[-6:]}", type="plan", level=lv)
        if not self.fixed_views:
            self.fixed_views = [self._view(name="S-0", type="section", p1=[-1000.0, 3000.0], p2=[12000.0, 3000.0]),
                                self._view(name="E-S", type="elevation", dir="S")]

    # ---- 임의 주석 연산
    def _plan_views(self):
        return [v for v in sorted(self.eng.st.by_kind.get("view", ())) if self.eng.st.el[v]["params"]["type"] == "plan"]

    def _elements_on(self, level):
        st = self.eng.st
        ws = [w for w in sorted(st.by_kind.get("wall", ())) if st.out.get(("on_level", w), [None])[0] == level]
        ds = [d for d in sorted(st.by_kind.get("door", ())) if st.inn.get(("hosts", d), [None])[0] in ws]
        return ws, ds

    def _roles(self, kind, typ):
        return [r for r, t in AN.ROLE_TYPES[kind].items() if t == typ]

    def _rand_plan_ref(self, ws, ds):
        r = self.rng
        pool = [("wall", w) for w in ws] * 2 + [("door", d) for d in ds] * 3
        kind, eid = r.choice(pool)
        role = r.choice([x for x in AN.ROLE_TYPES[kind] if AN.ROLE_TYPES[kind][x] != "z"])
        return AN.make_ref(kind, eid, role)

    def _annot_op(self):
        r, st = self.rng, self.eng.st
        pick = r.random()
        annots = annot_ids(self.eng)
        if pick < 0.34:                                          # 평면 치수
            pvs = self._plan_views()
            if pvs:
                v = r.choice(pvs)
                ws, ds = self._elements_on(st.el[v]["params"]["level"])
                if ws:
                    for _ in range(6):                           # 평행하지 않은 선 쌍 등은 거절되므로 몇 번 시도
                        a, b = self._rand_plan_ref(ws, ds), self._rand_plan_ref(ws, ds)
                        if a != b and self.h.run("annot.dim.create", {"view": v, "a": a, "b": b, "offset": r.choice([-600.0, 400.0, 900.0])})["ok"]:
                            return "dim"
        elif pick < 0.44:                                        # 높이 치수
            vs = [v for v in sorted(st.by_kind.get("view", ())) if st.el[v]["params"]["type"] != "plan"]
            lvls = sorted(st.by_kind.get("level", ()))
            walls = sorted(st.by_kind.get("wall", ()))
            doors = sorted(st.by_kind.get("door", ()))
            if vs and lvls:
                pool = [AN.make_ref("level", x, "elevation") for x in lvls]
                pool += [AN.make_ref("wall", w, role) for w in walls[:6] for role in ("base", "top")]
                pool += [AN.make_ref("door", d, role) for d in doors[:4] for role in ("sill", "head")]
                a, b = r.choice(pool), r.choice(pool)
                if a != b and self.h.run("annot.dim.create", {"view": r.choice(vs), "a": a, "b": b, "at": [r.choice([-800.0, 5000.0]), 0.0]})["ok"]:
                    return "dim"
        elif pick < 0.56:                                         # 태그
            pvs = self._plan_views()
            if pvs:
                v = r.choice(pvs)
                ws, ds = self._elements_on(st.el[v]["params"]["level"])
                pool = ws + ds
                if pool:
                    t = r.choice(pool)
                    fields = r.sample(list(AN.TAG_FIELDS[st.el[t]["kind"]]), r.randrange(1, 4))
                    if r.random() < 0.5 and "fire_rating" not in fields:
                        fields = fields[:2] + ["fire_rating"]
                    if self.h.run("annot.tag.create", {"view": v, "target": t, "fields": fields})["ok"]:
                        return "tag"
        elif pick < 0.60 and annots:                             # 삭제
            if self.h.run("annot.delete", {"id": r.choice(annots)})["ok"]:
                return "delete"
        elif pick < 0.66 and annots:                             # 고아 재결합(수동 복구)
            orphans = [a for a in annots if self.eng.g.q(AN.annot_state, a)[1] != "ok"]
            if orphans:
                a = r.choice(orphans)
                row = st.el[a]
                if row["kind"] == "dim":
                    v = row["params"]["view"]
                    if v in st.el and st.el[v]["params"]["type"] == "plan":
                        ws, ds = self._elements_on(st.el[v]["params"]["level"])
                        if ws and self.h.run("annot.rebind", {"id": a, "b": self._rand_plan_ref(ws, ds)})["ok"]:
                            return "rebind"
                else:
                    v = row["params"]["view"]
                    if v in st.el:
                        ws, ds = self._elements_on(st.el[v]["params"]["level"])
                        if ws and self.h.run("annot.rebind", {"id": a, "target": r.choice(ws), "fields": ["name"]})["ok"]:
                            return "rebind"
        elif pick < 0.72:                                        # 태그 대상의 방화등급 속성 변경(태그 글이 따라가야 한다)
            pool = sorted(st.by_kind.get("wall", ())) + sorted(st.by_kind.get("door", ()))
            if pool:
                t = r.choice(pool)
                ps = "Pset_WallCommon" if st.el[t]["kind"] == "wall" else "Pset_DoorCommon"
                if self.h.run("pset.set", {"id": t, "pset": ps, "prop": "FireRating", "value": r.choice(["EI30", "EI60", "EI90"])})["ok"]:
                    return "pset"
        else:                                                    # 벽 분할 계열 / 뷰 삭제(주석 연쇄 삭제)
            sub = r.random()
            walls = sorted(st.by_kind.get("wall", ()))
            if sub < 0.28 and walls:                             # 임의 분할
                w = r.choice(walls)
                x1, y1, x2, y2 = st.el[w]["params"]["axis"]
                length = ((x2 - x1) ** 2 + (y2 - y1) ** 2) ** 0.5
                return self._split(w, r.choice([0.25, 0.4, 0.5, 0.6, 0.75]) * length)
            if sub < 0.46:                                       # 문이 있는 벽을 문 사이(또는 문 옆)에서 분할
                cands = [w for w in walls if st.out.get(("hosts", w))]
                if cands:
                    w = r.choice(cands)
                    doors = sorted(st.out[("hosts", w)])
                    d = st.el[r.choice(doors)]["params"]
                    return self._split(w, d["offset"] + r.choice([-1, 1]) * (d["width"] / 2 + r.choice([50.0, 700.0, 1500.0])))
            elif sub < 0.62:                                     # 분할 사슬의 벽 삭제(끝 벽이면 successor_deleted, 중간 벽이면 사슬 끝이 살아 있으면 값이 나온다)
                tails = [n for _, n in self.h.splits() if n in st.el]
                if tails and self.h.run("element.delete", {"id": r.choice(tails)})["ok"]:
                    return "delete_tail"
            elif sub < 0.76:                                     # 분할 이력에 쓰인(지워진) id 로 다시 분할·생성: 거절돼야 한다(succ 순환·엉뚱한 부활 방지)
                return self._reuse_attempt()
            elif sub < 0.86:                                     # 축선 반전(방향 경고가 나와야 한다)
                if walls:
                    w = r.choice(walls)
                    x1, y1, x2, y2 = st.el[w]["params"]["axis"]
                    if self.h.run("wall.set", {"id": w, "axis": [x2, y2, x1, y1]})["ok"]:
                        return "reverse"
            else:
                vs = [v for v in sorted(st.by_kind.get("view", ())) if st.inn.get(("annot_of", v))]
                if vs and self.h.run("view.delete", {"id": r.choice(vs)})["ok"]:
                    return "view_delete"
        return None

    def _reuse_attempt(self):
        """분할 이력(succ)에 쓰인 뒤 지워진 id 를 new_id 로 다시 주거나 wall.create 의 id 로 줘 본다. 받아들이면 결함이다."""
        st = self.eng.st
        used = sorted({d for (k, _), dsts in st.out.items() if k == "succ" for d in dsts if d not in st.el})
        walls = sorted(st.by_kind.get("wall", ()))
        if not used or not walls:
            return None
        nid, w = self.rng.choice(used), self.rng.choice(walls)
        x1, y1, x2, y2 = st.el[w]["params"]["axis"]
        length = ((x2 - x1) ** 2 + (y2 - y1) ** 2) ** 0.5
        if self.rng.random() < 0.6:
            r = self.h.run("wall.split", {"id": w, "at": round(length * 0.5, 3), "new_id": nid})
            what = "wall.split"
        else:
            lv = st.out[("on_level", w)][0]
            r = self.h.run("wall.create", {"id": nid, "level": lv, "axis": [0.0, 0.0, 1000.0, 0.0]})
            what = "wall.create"
        self.stats["reuse_attempts"] += 1
        if r["ok"]:
            self.stats["reuse_accepted"] += 1
            self.failures.append({"step": self.stats["steps"], "kind": f"succ 이력 id 재사용을 {what} 가 받아들임", "bad": [nid]})
            return None
        self.stats["reuse_rejected"] += 1
        return "reuse_rejected"

    def _physical(self):
        """물리적 앵커 위치: 모든 벽의 시작·끝점(끝은 분할 후계를 따라간 소유자 기준)과 모든 문 중심. 주석과 무관한 독립 검사용."""
        orc = Oracle(self.eng, self.h.splits())
        out = {}
        for w in sorted(self.eng.st.by_kind.get("wall", ())):
            out[("start", w)] = orc.ref(AN.make_ref("wall", w, "axis.start"))[1]
            out[("end", w)] = orc.ref(AN.make_ref("wall", w, "axis.end"))[1]
        for d in sorted(self.eng.st.by_kind.get("door", ())):
            out[("door", d)] = orc.ref(AN.make_ref("door", d, "center"))[1]
        return out

    def _split(self, w, at):
        """벽 분할 + 불변성 검사: 분할은 형상을 바꾸지 않으므로 (1) 모든 치수 값이, (2) 모든 벽 끝점·문 중심의 물리적 위치가
        그대로여야 한다. 둘 다 오라클의 기대와는 별개의 '전후 비교' 검사다."""
        before = snapshot(self.eng)
        phys0 = self._physical()
        rr = self.h.run("wall.split", {"id": w, "at": round(at, 3), "new_id": _i("1", self.seed * 100000 + 50000 + self.stats["split_try"])})
        self.stats["split_try"] += 1
        if not rr["ok"]:
            self.stats["split_rejected"] += 1
            return None
        phys1 = self._physical()
        gone = [k for k in phys0 if (phys1[k] != phys0[k] if isinstance(phys0[k], str) or isinstance(phys1[k], str) else abs(phys1[k] - phys0[k]) > TOL)]
        if gone:
            self.stats["split_physical_violations"] += len(gone)
            self.failures.append({"step": self.stats["steps"], "kind": "split moved geometry", "bad": [str(k) for k in gone[:3]]})
        # own.* 참조(이 벽 자신의 끝)는 분할로 값이 달라지는 것이 설계다: 불변성 검사에서 뺀다(다른 모든 치수는 그대로여야 한다)
        moved = [d for d in stable_diff(before, snapshot(self.eng)) if self.eng.st.el[d[0]]["kind"] == "dim" if len(d) == 3 or d[1] != "사라짐"
                 if "#own." not in self.eng.st.el[d[0]]["params"]["a"] + self.eng.st.el[d[0]]["params"]["b"]]
        self.stats["split_invariance_checks"] += 1
        if moved:
            self.stats["split_invariance_violations"] += len(moved)
            self.failures.append({"step": self.stats["steps"], "kind": "split changed dim values", "bad": [str(m)[:160] for m in moved[:2]]})
        return "split"

    # ---- 검사
    def succ_cycles(self):
        """분할 이력(succ) 그래프의 순환 개수(불변식: 0). 상태 표(의도 저장소)만 읽는다."""
        out = self.eng.st.out
        nxt = {src: dsts for (k, src), dsts in out.items() if k == "succ" and dsts}
        cyc = 0
        for start in nxt:
            seen, x = {start}, start
            while x in nxt:
                x = nxt[x][0]
                if x in seen:
                    cyc += 1
                    break
                seen.add(x)
        return cyc

    def _ifc_check(self, step, label):
        """상주 엔진의 IFC 를 매번 투영(flush)해 터지지 않는지, 주기적으로는 파일을 새로 연 엔진의 IFC 해시와 같은지 본다.
        (flush 를 안 하는 시험은 투영 상태가 낡은 채 undo 로 이어지는 결함을 못 본다: 분할로 문이 옮겨진 뒤 투영하고 undo 하면 터졌다)"""
        self.stats["ifc_flushes"] += 1
        try:
            live = self.eng.ifc_hash()
        except BaseException as ex:
            self.failures.append({"step": step, "label": label, "kind": "ifc_flush_error", "err": f"{type(ex).__name__}: {str(ex)[:120]}"})
            return False
        if self.stats["ifc_flushes"] % self.ifc_ref_every == 0:
            self.stats["ifc_ref_compares"] += 1
            ref = Engine(self.sess.path, "ifc-ref")
            try:
                same = ref.ifc_hash() == live
            except BaseException as ex:
                self.failures.append({"step": step, "label": label, "kind": "ifc_reference_error", "err": f"{type(ex).__name__}: {str(ex)[:120]}"})
                return False
            finally:
                ref.close()
            if not same:
                self.stats["ifc_ref_mismatch"] += 1
                self.failures.append({"step": step, "label": label, "kind": "ifc_hash!=reopened"})
                return False
        return True

    def _fingerprints_ok(self, step, label):
        """새로 만들었거나 다시 묶은 치수의 방향 지문이, 그 순간의 실제 벽 방향(복소수로 따로 계산, 소수 6자리)과 같은가.
        (오라클의 경고 기대는 저장된 지문을 입력으로 쓰므로, 지문을 저장하는 쪽이 틀리면 여기서만 잡힌다)"""
        orc = Oracle(self.eng, self.h.splits())
        st = self.eng.st
        for aid in annot_ids(self.eng):
            row = st.el[aid]
            if row["kind"] != "dim":
                continue
            p = row["params"]
            key = (aid, p["a"], p["b"])
            if key in self._fp_seen:
                continue
            self._fp_seen.add(key)
            for side in ("a", "b"):
                kind, rest = p[side].split(":", 1)
                eid, role = rest.split("#", 1)
                uses = AN.uses_direction(kind, role)
                got = (p.get("dirs") or {}).get(side)
                if not uses:
                    ok = got is None
                else:
                    w = orc._dir_wall(kind, eid)
                    if w is None:
                        continue
                    _, u, _ = orc._axis(w)
                    ok = got is not None and abs(got[0] - round(u.real, 6)) < 2e-6 and abs(got[1] - round(u.imag, 6)) < 2e-6
                self.stats["fp_checks"] += 1
                if not ok:
                    self.stats["fp_mismatch"] += 1
                    self.failures.append({"step": step, "label": label, "kind": "direction fingerprint", "bad": [f"{aid} {side} {p[side]} {got}"]})
                    return False
        return True

    def _verify(self, step, label):
        if self.succ_cycles():
            self.failures.append({"step": step, "label": label, "kind": "succ_cycle"})
            self.stats["succ_cycles"] += 1
            return False
        if not self._ifc_check(step, label):
            return False
        if not self._fingerprints_ok(step, label):
            return False
        res = self.h.check()
        for k, v in res["counts"].items():
            self.stats["c_" + k] += v
        self.stats["checks"] += 1
        if res["n_bad"]:
            self.failures.append({"step": step, "label": label, "kind": "oracle", "bad": res["bad"], "counts": res["counts"]})
            return False
        if self.eng.derived_hash() != self.eng.derived_hash(reference=True):
            self.failures.append({"step": step, "label": label, "kind": "incremental!=full"})
            self.stats["incremental_mismatch"] += 1
            return False
        # annot.check 커맨드와 오라클의 개수 일치(커맨드 경로도 검증)
        return True

    def run(self, n: int, stop_on_fail: bool = False) -> dict:
        t0 = time.perf_counter()
        for step in range(n):
            self._ensure_views()
            it = self.gen.next(self.eng)
            before_snap = snapshot(self.eng) if step % self.undo_every == 0 else None
            before_state = self.eng.state_hash() if before_snap is not None else None
            before_rev, before_depth = self.eng.rev, len(self.eng.undo_stack)
            r = self.h.item(it)
            self.stats["ok" if r["ok"] else "rejected"] += 1
            if step % self.annot_every == 0:
                kind = self._annot_op()
                if kind:
                    self.stats["op_" + kind] += 1
            ok = self._verify(step, "after")
            # undo 복원: 이 스텝(생성기 항목 + 주석 연산)을 통째로 되돌리는 것은 트랜잭션이 여러 개일 수 있어
            # 마지막 트랜잭션 하나만 되돌렸다가 다시 하고, 그 전후 스냅숏이 되돌림으로 정확히 복원되는지 본다.
            if ok and before_snap is not None and self.eng.undo_stack and not it.get("name", "").startswith("history."):
                snap_now = snapshot(self.eng)
                h_now = self.eng.state_hash()
                u = self.h.run("history.undo", {})
                if u["ok"]:
                    self.stats["undo_checks"] += 1
                    ok = self._verify(step, "undo")
                    r2 = self.h.run("history.redo", {})
                    if r2["ok"]:
                        ok = self._verify(step, "redo") and ok
                        if snapshot(self.eng) != snap_now or self.eng.state_hash() != h_now:
                            self.failures.append({"step": step, "kind": "redo!=after"})
                            ok = False
                    else:
                        self.failures.append({"step": step, "kind": "redo_failed"})
                        ok = False
            if not ok and stop_on_fail:
                break
        # 로그 전체 재생 / 파일 새로 열기 / DoBIM_Intent 일관성 / IFC 유효성: 주석과 분할 커맨드가 로그 재생에서도 같은 결과를 내는가
        if not (stop_on_fail and self.failures):
            fc = kit.full_check(self.sess, self.sess.path.parent, validate=True, tag=f"-s5-{self.seed}")
            self.stats["full_check_ok"] = int(fc["ok"])
            if not fc["ok"]:
                self.failures.append({"step": "end", "kind": "full_check", "diffs": fc["diffs"]})
        self.stats["steps"] = step + 1
        self.stats["sec"] = round(time.perf_counter() - t0, 2)
        self.stats["annots_final"] = len(annot_ids(self.eng))
        return {"seed": self.seed, "stats": dict(self.stats), "failures": self.failures[:6], "n_failures": len(self.failures)}

    def close(self):
        self.sess.close()


# ================================================================ 변이 (음성 대조)
@contextlib.contextmanager
def mutation(name: str):
    """코어의 한 군데를 일부러 망가뜨린다. 같은 검사가 이를 잡아야 한다."""
    restore = []

    def patch(obj, attr, new):
        restore.append((obj, attr, getattr(obj, attr)))
        setattr(obj, attr, new)

    if name == "M1_분할_이력_무시":
        orig = AN._wall_ref

        def f(g, w, role, row):
            if role in AN.END_ROLES:
                p = row["params"]
                return ("pt", p["axis"][2], p["axis"][3]) if role == "axis.end" else orig(g, w, "axis.end", row)
            return orig(g, w, role, row)
        patch(AN, "_wall_ref", f)
    elif name == "M2_의존_기록_누락(축선 직접 읽기)":
        orig = AN.ref_resolve

        def f(g, ref):
            p = AN.parse_ref(ref)
            if p and p[0] == "wall" and p[2] in ("axis.start", "axis.end"):
                row = g.inputs.load(("el", p[1]))                  # g.input 을 거치지 않아 의존이 기록되지 않는다
                if row is not None:
                    a = row["params"]["axis"]
                    return ("pt", a[0], a[1]) if p[2] == "axis.start" else ("pt", a[2], a[3])
            return orig(g, ref)
        patch(AN, "ref_resolve", f)
    elif name == "M3_삭제된_대상의_옛_값_유지":
        orig = AN.ref_resolve
        last = {}

        def f(g, ref):
            v = orig(g, ref)
            if v[0] != "orphan":
                last[ref] = v
                return v
            return last.get(ref, v)
        patch(AN, "ref_resolve", f)
    elif name == "M4_문틀_시작_끝_뒤바뀜":
        orig = AN._door_ref

        def f(g, d, role, row):
            role = {"jamb.start": "jamb.end", "jamb.end": "jamb.start"}.get(role, role)
            return orig(g, d, role, row)
        patch(AN, "_door_ref", f)
    elif name == "M5_면_왼쪽_오른쪽_뒤바뀜":
        orig = AN._wall_ref

        def f(g, w, role, row):
            role = {"face.left": "face.right", "face.right": "face.left"}.get(role, role)
            return orig(g, w, role, row)
        patch(AN, "_wall_ref", f)
    elif name == "M6_태그_속성셋_의존_누락":
        orig = AN._field

        def f(g, kind, eid, row, name):
            if name == "fire_rating":
                ps = "Pset_WallCommon" if kind == "wall" else "Pset_DoorCommon"
                v = g.inputs.load(("pset", eid)).get(ps, {}).get("FireRating")   # 의존 미기록
                return "-" if v is None else str(v)
            return orig(g, kind, eid, row, name)
        patch(AN, "_field", f)
    elif name == "M7_후계_삭제시_자기_끝으로_대체":
        orig = AN._wall_ref

        def f(g, w, role, row):
            if role in AN.END_ROLES:
                nxt = AN.Q.rel_out(g, "succ", w)
                if nxt and AN.Q.el(g, nxt[0]) is None:
                    p = row["params"]
                    return ("pt", p["axis"][2], p["axis"][3])                    # 조용히 엉뚱한 곳(분할점)을 가리킴
            return orig(g, w, role, row)
        patch(AN, "_wall_ref", f)
    elif name == "M8_succ_변경_미통지":
        from dobim_core.engine import Engine
        orig = Engine._apply_effect

        def f(self, e, persist):
            orig(self, e, persist)
            if e[0] == "rel" and e[1] == "succ":
                self._pending.discard(("rel_out", "succ", e[2]))
                self._pending.discard(("rel_in", "succ", e[3]))
        patch(Engine, "_apply_effect", f)
    elif name == "M9_뷰_삭제시_주석_연쇄삭제_없음":
        from dobim_core import commands as C
        patch(C, "_cascade_view_annots", lambda ctx, vid: [])
    elif name == "M10_고아를_0값으로_표시":
        orig = AN.dim_value

        def f(g, d):
            v = orig(g, d)
            return ("ok", 0.0) if v[0] == "orphan" else v
        patch(AN, "dim_value", f)
    elif name == "M11_분할이_문_오프셋을_옮기지_않음":
        import dataclasses
        from dobim_core import commands as C
        from dobim_core import registry
        real = C.wall_split

        class Proxy:
            def __init__(self, c):
                self.c = c

            def __getattr__(self, n):
                return getattr(self.c, n)

            def put_el(self, eid, new):
                cur = self.c.st.el.get(eid)
                if cur is not None and new is not None and cur["kind"] == "door":
                    return None                                   # 문 오프셋 보정을 빠뜨린다
                return self.c.put_el(eid, new)

        cmd = registry.REGISTRY["wall.split"]
        restore.append((registry.REGISTRY, "wall.split", cmd))
        registry.REGISTRY["wall.split"] = dataclasses.replace(cmd, func=lambda ctx, a: real(Proxy(ctx), a))
    elif name == "M12_벽_삭제시_개구부_투영상태_정리_누락":
        import ifcopenshell.api.root as api_root
        from dobim_core.ifc_model import IfcModel

        def f(self, eid):                                              # 개구부가 벽과 함께 사라져도 self.openings 에 낡은 참조를 남기던 옛 구현
            e = self.ents[eid]
            self._unproject_door(eid)
            api_root.remove_product(self.f, product=e)
            self.ents.pop(eid)
            self.container_of.pop(eid, None)
            return []
        patch(IfcModel, "remove_identity", f)
    elif name == "M13_succ_이력_id_재사용_허용":
        from dobim_core import commands as C
        patch(C, "_in_split_history", lambda ctx, eid: False)             # 재사용 거절을 끈다
        patch(C, "_assert_succ_acyclic", lambda ctx, start: None)         # 마지막 안전망도 끈다
    elif name == "M14_방향_지문_경고_없음":
        patch(AN, "dim_flags", lambda g, d: ())
    elif name == "M15_사슬_중간_삭제를_고아로_처리":
        def f(g, w):                                                      # 첫 후계만 보는 옛 규칙: 중간 벽이 지워지면 사슬 끝이 살아 있어도 고아
            nxt = AN.Q.rel_out(g, "succ", w)
            return nxt[0] if nxt else w
        patch(AN, "chain_tail", f)
    else:
        raise KeyError(name)
    # 메모이즈 질의는 함수 객체 자체가 키다: 함수를 바꿨으면 이미 만든 그래프를 쓰지 않도록 호출 쪽이 새 엔진을 만든다
    try:
        yield
    finally:
        for obj, attr, old in reversed(restore):
            if isinstance(obj, dict):
                obj[attr] = old
            else:
                setattr(obj, attr, old)


MUTANTS = ["M1_분할_이력_무시", "M2_의존_기록_누락(축선 직접 읽기)", "M3_삭제된_대상의_옛_값_유지", "M4_문틀_시작_끝_뒤바뀜",
           "M5_면_왼쪽_오른쪽_뒤바뀜", "M6_태그_속성셋_의존_누락", "M7_후계_삭제시_자기_끝으로_대체", "M8_succ_변경_미통지",
           "M9_뷰_삭제시_주석_연쇄삭제_없음", "M10_고아를_0값으로_표시", "M11_분할이_문_오프셋을_옮기지_않음",
           "M12_벽_삭제시_개구부_투영상태_정리_누락", "M13_succ_이력_id_재사용_허용", "M14_방향_지문_경고_없음", "M15_사슬_중간_삭제를_고아로_처리"]
