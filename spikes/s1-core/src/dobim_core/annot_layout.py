# SPDX-License-Identifier: MPL-2.0
"""태그 겹침 회피 배치(W2 스물여섯 번째 PR, Revit 의 태그 자리 잡기·지시선 대응). 순수 계산(시각·난수·파일 없음).

뷰 하나의 태그 글 상자가 서로, 그리고 치수 글·치수선·문자 주석(방 이름·작도 문자)과 겹치지 않는 자리를 고른다.
같은 입력이면 항상 같은 출력이다: 태그를 (기준점 x, y, id) 순으로 하나씩 놓고, 후보를 정해진 순서로 보아 처음 겹침이 없는 후보를 쓴다.

후보 자리(기준점에서 글 자리까지의 간격 off, 용지 mm 를 축척으로 모델 mm 로 옮긴다):
  0) 지금 자리(손으로 옮기지 않은 태그가 겹치지 않으면 그대로 둔다: 옮김을 최소로 하고 다시 실행해도 같은 결과)
  지시선 태그(가로 글): 8방향 중 7방향(NE, E, SE, N, NW, W, SW; 남쪽은 지시선이 글을 가로지르므로 뺀다) x 고리 5개
      (기준점에서 6·11·16·21·26 mm). 서쪽 후보는 글을 오른쪽 맞춤(side end), 북쪽은 가운데 맞춤(side middle)으로 놓아 지시선이 글 쪽에서 들어온다.
  보 축 방향 태그(글이 보를 따라 눕는다): 먼저 보 위에서 보를 따라 ±1.0w·1.5w·…·4.0w(w = 글 폭, 0.5w 간격, + 먼저) 미끄러지고, 그다음 글 위쪽(보 진행 방향 왼쪽,
      글이 거꾸로 서지 않는 쪽) 5·9·13·17·21 mm 고리 x 보를 따라 0·+1.1w·-1.1w. 보 가운데선에서 위로 들린 태그만 지시선(부착 끝점)이 켜진다.
겹침 판정: 글 상자(축 정렬 경계 상자, 기울어진 글은 네 모서리의 경계 상자)를 0.6 mm(용지) 부풀려 겹치면 충돌. 치수선·보조선·이미 놓은 지시선이
  글 상자를 지나가는 것과, 내 지시선이 다른 글 상자를 지나가는 것도 충돌이다.
후보가 모두 충돌이면 충돌 넓이(+ 선이 지나가는 횟수 x 큰 값)가 가장 작은 후보를 쓰고(같으면 앞선 후보) 해결하지 못한 태그로 알린다."""
from __future__ import annotations

from math import atan2, cos, degrees, radians, sin, sqrt

TAG_H = 2.5      # 태그 글 높이(용지 mm). 내보내기의 태그 글 크기와 같다
GAP = 0.6        # 지시선 태그 글이 글 자리에서 떨어진 거리(용지 mm). 내보내기의 +0.6 과 같다
PAD = 0.6        # 겹침 여유(용지 mm)
CROSS_COST = 1e9  # 선이 글 상자를 지나가는 충돌 한 번의 점수(넓이 mm² 보다 항상 크게)
LEADER_RINGS = (6.0, 11.0, 16.0, 21.0, 26.0)
AXIS_RINGS = (5.0, 9.0, 13.0, 17.0, 21.0)
SLIDE_STEPS = (1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0)   # 보를 따라 미끄러지는 거리(글 폭 배수): 이웃 글 폭이 달라도 한 칸은 비는 간격이 나오게 0.5w 씩
# (x 부호, y 부호, 글 맞춤). 남쪽(0, -1)은 뺀다
LEADER_DIRS = ((1, 1, "start"), (1, 0, "start"), (1, -1, "start"), (0, 1, "middle"), (-1, 1, "end"), (-1, 0, "end"), (-1, -1, "end"))


def label_width(s: str, size: float) -> float:
    """문자 층 글의 대략 폭(용지 mm). 정확한 폭은 글꼴이 정하므로 배치·범위 계산에만 쓴다(drawing_export.label_width 와 같은 규칙, 시험이 지킨다)."""
    return sum(size * (1.0 if ord(c) >= 0x2E80 else 0.58) for c in s)


def tag_parts(it):
    """태그 그림 항목(5·6·8칸)을 (기준점, 글 자리, 글, 보 방향 | None, 지시선 점들, 글 맞춤)으로 푼다. 내보내기·장면이 한 규칙으로 읽는다.
    5칸 = 지시선 가로 글(기준점에서 글 자리까지 지시선), 6칸 = 보 축 방향 글(지시선 없음), 8칸 = 지시선·글 자리 옵션이 있는 태그."""
    n = len(it)
    if n > 7:
        return it[2], it[3], it[4], it[5], it[6], it[7]
    if n > 5:
        return it[2], it[3], it[4], it[5], (), "start"
    return it[2], it[3], it[4], None, (tuple(it[2]), tuple(it[3])), "start"


def text_angle(u) -> float:
    """눕힌 글의 용지 회전각(도): 방향 u(뷰 좌표 단위 벡터)에서 글이 거꾸로 서지 않게 (-90, 90] 로 맞춘다(drawing_export.adim_text_angle 과 같다)."""
    ang = degrees(atan2(-u[1], u[0]))
    return ang - 180.0 if ang > 90.0 else (ang + 180.0 if ang <= -90.0 else ang)


def text_frame(u):
    """눕힌 글의 (진행 방향 t, 위쪽 n) 뷰 좌표 단위 벡터. SVG 의 rotate(a) 는 용지(y 아래)에서 시계 방향이라 뷰 좌표에서 t = (cos a, -sin a)."""
    a = radians(text_angle(u))
    return (cos(a), -sin(a)), (sin(a), cos(a))


def tag_box(lab, text, u, side, scale):
    """태그 글의 경계 상자 (x0, y0, x1, y1)(뷰 좌표 mm). u = 보 진행 방향(눕힌 글) 또는 None(가로 글).
    가로 글: 기준선이 글 자리 높이, 글자는 기준선 위 0.8 em·아래 0.2 em. 눕힌 글: 가운데 맞춤, 기준선이 글 자리에서 글 위쪽으로 0.8 mm(용지) 올라선다."""
    h, w = TAG_H * scale, label_width(text, TAG_H) * scale
    if u is None:
        gap = GAP * scale
        if side == "end":
            x0, x1 = lab[0] - gap - w, lab[0] - gap
        elif side == "middle":
            x0, x1 = lab[0] - w / 2.0, lab[0] + w / 2.0
        else:
            x0, x1 = lab[0] + gap, lab[0] + gap + w
        return (x0, lab[1] - 0.2 * h, x1, lab[1] + 0.8 * h)
    t, n = text_frame(u)
    b = 0.8 * scale
    xs, ys = [], []
    for a in (-w / 2.0, w / 2.0):
        for c in (b - 0.2 * h, b + 0.8 * h):
            xs.append(lab[0] + a * t[0] + c * n[0])
            ys.append(lab[1] + a * t[1] + c * n[1])
    return (min(xs), min(ys), max(xs), max(ys))


def text_box_at(pos, text, size, anchor, ang_rot, scale):
    """다른 문자(방 이름·작도 문자·치수 글)의 경계 상자. size = 글 높이(용지 mm), anchor = start·middle·end, ang_rot = 용지 회전각(도, 시계 방향, 0 이면 가로).
    기준선이 pos 이고 글자는 기준선 위 0.8 em·아래 0.2 em 이다. 기울어진 글은 네 모서리의 경계 상자."""
    h, w = size * scale, label_width(text, size) * scale
    x0 = {"start": 0.0, "middle": -w / 2.0, "end": -w}[anchor]
    a = radians(ang_rot)
    t, n = (cos(a), -sin(a)), (sin(a), cos(a))
    xs, ys = [], []
    for ax in (x0, x0 + w):
        for c in (-0.2 * h, 0.8 * h):
            xs.append(pos[0] + ax * t[0] + c * n[0])
            ys.append(pos[1] + ax * t[1] + c * n[1])
    return (min(xs), min(ys), max(xs), max(ys))


def grow(b, m):
    return (b[0] - m, b[1] - m, b[2] + m, b[3] + m)


def overlap_area(a, b) -> float:
    w = min(a[2], b[2]) - max(a[0], b[0])
    h = min(a[3], b[3]) - max(a[1], b[1])
    return w * h if w > 0.0 and h > 0.0 else 0.0


def seg_hits_box(s, b) -> bool:
    """선분 s = (x0, y0, x1, y1) 이 상자 b 안을 지나는가(Liang-Barsky, 스치기만 하는 경계는 지나는 것으로 보지 않는다)."""
    x0, y0, dx, dy = s[0], s[1], s[2] - s[0], s[3] - s[1]
    t0, t1 = 0.0, 1.0
    for p, q in ((-dx, x0 - b[0]), (dx, b[2] - x0), (-dy, y0 - b[1]), (dy, b[3] - y0)):
        if p == 0.0:
            if q <= 0.0:
                return False
        else:
            r = q / p
            if p < 0.0:
                if r > t1:
                    return False
                t0 = max(t0, r)
            else:
                if r < t0:
                    return False
                t1 = min(t1, r)
    return t1 > t0


class _Index:
    """상자·선분을 격자 칸에 나눠 담아 가까운 것만 찾는다(칸 크기 = 용지 30 mm)."""

    def __init__(self, scale):
        self.c = 30.0 * scale
        self.boxes: list = []
        self.segs: list = []
        self._bg: dict = {}
        self._sg: dict = {}

    def _cells(self, x0, y0, x1, y1):
        c = self.c
        return [(i, j) for i in range(int(x0 // c), int(x1 // c) + 1) for j in range(int(y0 // c), int(y1 // c) + 1)]

    def add_box(self, b):
        self.boxes.append(b)
        for k in self._cells(*b):
            self._bg.setdefault(k, []).append(len(self.boxes) - 1)

    def add_seg(self, s):
        self.segs.append(s)
        for k in self._cells(min(s[0], s[2]), min(s[1], s[3]), max(s[0], s[2]), max(s[1], s[3])):
            self._sg.setdefault(k, []).append(len(self.segs) - 1)

    def near_boxes(self, x0, y0, x1, y1):
        seen = set()
        for k in self._cells(x0, y0, x1, y1):
            seen.update(self._bg.get(k, ()))
        return [self.boxes[i] for i in sorted(seen)]

    def near_segs(self, x0, y0, x1, y1):
        seen = set()
        for k in self._cells(x0, y0, x1, y1):
            seen.update(self._sg.get(k, ()))
        return [self.segs[i] for i in sorted(seen)]


def _score(ix: _Index, box, lead, scale) -> float:
    """후보 하나의 충돌 점수(0 이면 겹침 없음): 글 상자끼리 겹친 넓이 + 선이 글 상자를 지나가는 횟수 x CROSS_COST."""
    pad = grow(box, PAD * scale)
    s = 0.0
    for b in ix.near_boxes(*pad):
        s += overlap_area(pad, grow(b, PAD * scale))
    for sg in ix.near_segs(*pad):
        if seg_hits_box(sg, pad):
            s += CROSS_COST
    if lead is not None:
        lb = (min(lead[0], lead[2]), min(lead[1], lead[3]), max(lead[0], lead[2]), max(lead[1], lead[3]))
        for b in ix.near_boxes(*lb):
            if seg_hits_box(lead, grow(b, PAD * scale * 0.5)):
                s += CROSS_COST
    return s


def lead_on(u, off) -> bool:
    """배치 뒤 지시선이 켜지는가: 지시선 태그(가로 글)는 늘 켜짐, 보 축 방향 태그는 보 가운데선에서 위로 들린 때만(보를 따라 미끄러진 것은 보 위라 필요 없다)."""
    if u is None:
        return True
    _t, n = text_frame(u)
    return abs(off[0] * n[0] + off[1] * n[1]) > 1e-6


def lead_start(u, off):
    """배치가 정하는 지시선 시작점의 대표점 기준 간격: 지시선 태그는 대표점(부착), 들린 보 태그는 글 바로 밑의 보 위 점(대표점 + 보를 따라 잰 간격, 자유 끝점)이라
    지시선이 보에서 곧게 글로 올라간다."""
    if u is None:
        return (0.0, 0.0)
    t, _n = text_frame(u)
    a = off[0] * t[0] + off[1] * t[1]
    return (round(a * t[0], 3) + 0.0, round(a * t[1], 3) + 0.0)


def candidates(tag, scale):
    """태그 하나의 후보 [(간격 off, 글 맞춤 side)]: 지금 자리가 맨 앞, 그다음 정해진 순서. 중복은 뺀다."""
    out = [(tuple(tag["off"]), tag["side"])]
    if tag["dir"] is None:
        for r in LEADER_RINGS:
            for sx, sy, side in LEADER_DIRS:
                k = r * scale / sqrt(sx * sx + sy * sy)
                out.append(((round(sx * k, 3) + 0.0, round(sy * k, 3) + 0.0), side))
    else:
        t, n = text_frame(tag["dir"])
        w = label_width(tag["text"], TAG_H) * scale
        span = tag.get("span")   # 보에 남은 길이의 절반(글이 보 밖으로 나가지 않게): 없으면 제한 없음
        fits = lambda a: span is None or abs(a) + w / 2.0 <= span + 1e-6   # noqa: E731
        for m in SLIDE_STEPS:   # 보 위에서 보를 따라 미끄러짐(보 가운데선 위라 지시선이 필요 없다): 글 폭의 m 배, 부호는 + 먼저
            for k in (m, -m):
                if fits(k * w):
                    out.append(((round(k * w * t[0], 3) + 0.0, round(k * w * t[1], 3) + 0.0), "start"))
        for r in AXIS_RINGS:
            for a in (0.0, 1.1 * w, -1.1 * w):
                if fits(a):
                    out.append(((round(a * t[0] + r * scale * n[0], 3) + 0.0, round(a * t[1] + r * scale * n[1], 3) + 0.0), "start"))
    seen, res = set(), []
    for o, sd in out:
        if (o, sd) not in seen:
            seen.add((o, sd))
            res.append((o, sd))
    return res


def arrange(tags, fixed_boxes, fixed_segs, scale):
    """태그들의 자리를 정한다.
    tags: [{"id", "anc": (x, y), "text", "dir": 보 방향 | None, "off": 지금 간격, "side": 지금 글 맞춤, "span": 보에 남은 길이의 절반 | None(선택)}]
          (이 순서와 무관: 안에서 (x, y, id) 순으로 정렬한다)
    fixed_boxes: 움직이지 않는 글 상자들(손으로 옮긴 태그·치수 글·문자), fixed_segs: 움직이지 않는 선분들(치수선·보조선·고정 태그 지시선)
    반환: ({id: (off, side)}, 해결하지 못한 id 목록(정렬))"""
    ix = _Index(scale)
    for b in fixed_boxes:
        ix.add_box(b)
    for s in fixed_segs:
        ix.add_seg(s)
    res, bad = {}, []
    for tg in sorted(tags, key=lambda t: (round(t["anc"][0], 3), round(t["anc"][1], 3), t["id"])):
        ax, ay = tg["anc"]
        best = None
        for k, (off, side) in enumerate(candidates(tg, scale)):
            lab = (ax + off[0], ay + off[1])
            box = tag_box(lab, tg["text"], tg["dir"], side, scale)
            st = lead_start(tg["dir"], off)
            lead = (ax + st[0], ay + st[1], lab[0], lab[1]) if lead_on(tg["dir"], off) else None
            sc = _score(ix, box, lead, scale)
            if best is None or sc < best[0]:
                best = (sc, off, side, box, lead)
            if sc == 0.0:
                break
        sc, off, side, box, lead = best
        res[tg["id"]] = (off, side)
        if sc > 0.0:
            bad.append(tg["id"])
        ix.add_box(box)
        if lead is not None:
            ix.add_seg(lead)
    return res, sorted(bad)
