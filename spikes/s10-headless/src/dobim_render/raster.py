# SPDX-License-Identifier: MPL-2.0
"""순수 numpy 소프트웨어 z-버퍼 래스터라이저: 평면 음영 + 특징선 + 요소 ID 버퍼.

결정성 원칙(플랫폼·CPU 와 무관하게 같은 픽셀):
- 삼각함수·행렬곱(BLAS)·reduction(sum/dot)을 쓰지 않는다. 사칙연산과 sqrt 만 쓴다(IEEE 754 로 정확히 반올림되는 연산).
- 삼각형은 입력 순서대로 그리고 깊이가 엄격히 더 가까울 때만 덮는다(동률은 먼저 그린 쪽이 이긴다).
- 음영은 면마다 정수(0..256)로 양자화하고 픽셀 색은 정수 연산으로만 만든다.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

VIEWS = {  # 장면 중심에서 카메라 쪽 방향
    "iso": (1.0, -1.0, 0.9),
    "top": (0.0, 0.0, 1.0),
    "south": (0.0, -1.0, 0.0),
    "east": (1.0, 0.0, 0.0),
}
LIGHT = (-0.35, -0.55, 0.76)   # 빛이 오는 쪽(카메라 방향과 별개의 고정 방향)
BG = (245, 245, 245)
LINE = (35, 35, 40)
CREASE_COS = 0.90              # 인접 픽셀 면 법선이 이보다 더 벌어지면 특징선
PALETTE = {
    "IfcWall": (205, 198, 186), "IfcWallStandardCase": (205, 198, 186), "IfcSlab": (168, 170, 176),
    "IfcColumn": (150, 156, 170), "IfcBeam": (160, 150, 140), "IfcDoor": (150, 98, 58),
    "IfcWindow": (118, 170, 212), "IfcStairFlight": (190, 170, 140), "IfcStair": (190, 170, 140),
    "IfcRoof": (176, 120, 110), "IfcCovering": (200, 200, 190), "IfcFurnishingElement": (140, 160, 130),
}
DEFAULT_COLOR = (182, 182, 182)


def _norm3(a, b, c):
    n = (a * a + b * b + c * c) ** 0.5
    return a / n, b / n, c / n


def camera_basis(view: str):
    e = _norm3(*VIEWS[view])
    up = (0.0, 0.0, 1.0) if abs(e[2]) < 0.999 else (0.0, 1.0, 0.0)
    r = _norm3(up[1] * e[2] - up[2] * e[1], up[2] * e[0] - up[0] * e[2], up[0] * e[1] - up[1] * e[0])
    u = (e[1] * r[2] - e[2] * r[1], e[2] * r[0] - e[0] * r[2], e[0] * r[1] - e[1] * r[0])
    return r, u, e


def dot3(v: np.ndarray, b) -> np.ndarray:
    """(N,3) 와 상수 3-벡터의 내적. 순서 고정 사칙연산(BLAS 안 씀)."""
    return v[:, 0] * b[0] + v[:, 1] * b[1] + v[:, 2] * b[2]


@dataclass
class Frame:
    color: np.ndarray    # (H, W, 3) uint8
    ids: np.ndarray      # (H, W) int32 요소 인덱스, 배경 -1
    faces: np.ndarray    # (H, W) int32 면 인덱스, 배경 -1
    depth: np.ndarray    # (H, W) float64, 클수록 가깝다, 배경 -inf
    stats: dict


def face_normals(verts: np.ndarray, faces: np.ndarray) -> np.ndarray:
    a, b, c = verts[faces[:, 0]], verts[faces[:, 1]], verts[faces[:, 2]]
    u, v = b - a, c - a
    n = np.stack([u[:, 1] * v[:, 2] - u[:, 2] * v[:, 1], u[:, 2] * v[:, 0] - u[:, 0] * v[:, 2],
                  u[:, 0] * v[:, 1] - u[:, 1] * v[:, 0]], axis=1)
    ln = np.sqrt(n[:, 0] * n[:, 0] + n[:, 1] * n[:, 1] + n[:, 2] * n[:, 2])
    ln[ln == 0] = 1.0
    return n / ln[:, None]


def render(verts: np.ndarray, faces: np.ndarray, face_elem: np.ndarray, elem_classes: list[str],
           width: int = 1280, height: int | None = None, view: str = "iso", margin: int = 24) -> Frame:
    height = height or (width * 5) // 8
    H, W = height, width
    stats: dict = {"triangles": int(len(faces)), "width": W, "height": H, "view": view}
    r, u, e = camera_basis(view)
    if len(faces) == 0:
        empty = np.empty((H, W, 3), np.uint8)
        empty[:] = BG
        return Frame(empty, np.full((H, W), -1, np.int32), np.full((H, W), -1, np.int32),
                     np.full((H, W), -np.inf), stats)
    x = dot3(verts, r)
    y = dot3(verts, u)
    z = dot3(verts, e)
    x0, x1, y0, y1 = x.min(), x.max(), y.min(), y.max()
    ex, ey = max(x1 - x0, 1e-9), max(y1 - y0, 1e-9)
    scale = min((W - 2 * margin) / ex, (H - 2 * margin) / ey)
    cx = (W - (x1 - x0) * scale) / 2 - x0 * scale          # 화면 가운데 맞춤
    cy = (H - (y1 - y0) * scale) / 2 + y1 * scale
    sx = x * scale + cx
    sy = cy - y * scale
    normals = face_normals(verts, faces)
    zr = float(z.max() - z.min())

    # ---- 래스터화 (삼각형 순서대로)
    zbuf = np.full((H, W), -np.inf)
    idb = np.full((H, W), -1, np.int32)
    fib = np.full((H, W), -1, np.int32)
    px_all = np.arange(W, dtype=np.float64) + 0.5
    py_all = np.arange(H, dtype=np.float64) + 0.5
    AX, BX, CX = sx[faces[:, 0]], sx[faces[:, 1]], sx[faces[:, 2]]
    AY, BY, CY = sy[faces[:, 0]], sy[faces[:, 1]], sy[faces[:, 2]]
    AZ, BZ, CZ = z[faces[:, 0]], z[faces[:, 1]], z[faces[:, 2]]
    area = (BX - AX) * (CY - AY) - (BY - AY) * (CX - AX)
    minx = np.floor(np.minimum(np.minimum(AX, BX), CX)).astype(np.int64).clip(0, W)
    maxx = (np.floor(np.maximum(np.maximum(AX, BX), CX)).astype(np.int64) + 1).clip(0, W)
    miny = np.floor(np.minimum(np.minimum(AY, BY), CY)).astype(np.int64).clip(0, H)
    maxy = (np.floor(np.maximum(np.maximum(AY, BY), CY)).astype(np.int64) + 1).clip(0, H)
    lists = [a.tolist() for a in (AX, AY, BX, BY, CX, CY, AZ, BZ, CZ, area, minx, maxx, miny, maxy)]
    fe = face_elem.tolist()
    drawn = 0
    for i, (ax, ay, bx, by, cx_, cy_, az, bz, cz, ar, xa, xb, ya, yb) in enumerate(zip(*lists)):
        if xb <= xa or yb <= ya or abs(ar) < 1e-12:
            continue
        pxs = px_all[xa:xb][None, :]
        pys = py_all[ya:yb][:, None]
        # 가장자리 함수 / 면적 = 무게중심 좌표
        # b->c 변의 가장자리 함수 E_bc(p) = (cx-bx)*(py-by) - (cy-by)*(px-bx)
        wa = ((cx_ - bx) * (pys - by) - (cy_ - by) * (pxs - bx)) / ar
        wb = ((ax - cx_) * (pys - cy_) - (ay - cy_) * (pxs - cx_)) / ar
        wc = ((bx - ax) * (pys - ay) - (by - ay) * (pxs - ax)) / ar
        inside = (wa >= 0) & (wb >= 0) & (wc >= 0)
        if not inside.any():
            continue
        d = wa * az + wb * bz + wc * cz
        sub = zbuf[ya:yb, xa:xb]
        upd = inside & (d > sub)
        if not upd.any():
            continue
        sub[upd] = d[upd]
        idb[ya:yb, xa:xb][upd] = fe[i]
        fib[ya:yb, xa:xb][upd] = i
        drawn += 1
    stats["triangles_drawn"] = drawn

    # ---- 음영: 면마다 0..256 정수
    nv = normals * np.where(dot3(normals, e) < 0, -1.0, 1.0)[:, None]   # 보는 쪽으로 뒤집기(양면)
    lam = np.clip(dot3(nv, _norm3(*LIGHT)), 0.0, 1.0)
    shade = np.floor((0.45 + 0.55 * lam) * 256.0).astype(np.int32).clip(0, 256)
    base = np.array([PALETTE.get(c, DEFAULT_COLOR) for c in elem_classes], dtype=np.int32)
    color = np.empty((H, W, 3), np.uint8)
    color[:] = BG
    m = fib >= 0
    ff = fib[m]
    color[m] = ((base[idb[m]] * shade[ff][:, None]) >> 8).astype(np.uint8)

    # ---- 특징선: 요소 경계(실루엣 포함) + 법선이 크게 꺾인 모서리 + 깊이 불연속
    zthr = max(zr, 1e-6) * 0.02
    edge = np.zeros((H, W), bool)
    for sl_a, sl_b in (((slice(0, H), slice(0, W - 1)), (slice(0, H), slice(1, W))),
                       ((slice(0, H - 1), slice(0, W)), (slice(1, H), slice(0, W)))):
        ia, ib = idb[sl_a], idb[sl_b]
        fa, fb = fib[sl_a], fib[sl_b]
        both = (fa >= 0) & (fb >= 0)
        diff_id = ia != ib
        na, nb = normals[fa.clip(0)], normals[fb.clip(0)]
        cosv = np.abs(na[..., 0] * nb[..., 0] + na[..., 1] * nb[..., 1] + na[..., 2] * nb[..., 2])
        crease = both & (fa != fb) & (ia == ib) & (cosv < CREASE_COS)
        za, zb = np.where(both, zbuf[sl_a], 0.0), np.where(both, zbuf[sl_b], 0.0)   # 배경(-inf) 뺄셈 경고 방지
        dz = both & (np.abs(za - zb) > zthr)
        edge[sl_a] |= diff_id | crease | dz
    color[edge] = LINE
    stats["edge_pixels"] = int(edge.sum())
    stats["covered_pixels"] = int(m.sum())
    return Frame(color, idb, fib, zbuf, stats)
