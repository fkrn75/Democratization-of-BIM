# SPDX-License-Identifier: MPL-2.0
"""IFC -> 삼각형 메시 (ifcopenshell.geom). 이 모듈만 ifcopenshell 을 import 한다.

결정성을 위해: 요소를 GlobalId 순으로 정렬해 이어 붙이고, 좌표를 1e-6 m 단위로 반올림한다
(플랫폼별 OCCT 메시 수치 잡음 제거). 스레드 수와 무관하게 같은 메시가 나와야 한다(시험).
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

import numpy as np

# 렌더하지 않는 클래스: 공간(투명 볼륨)과 개구부(빼기 형상)
SKIP_CLASSES = ("IfcSpace", "IfcOpeningElement", "IfcVirtualElement")
QUANT = 1_000_000  # 1e-6 m


@dataclass
class Mesh:
    verts: np.ndarray            # (N, 3) float64, 미터
    faces: np.ndarray            # (M, 3) int32
    face_elem: np.ndarray        # (M,) int32 — elems 인덱스
    elems: list[tuple[str, str]] = field(default_factory=list)   # (GlobalId, IFC 클래스)

    def sha256(self) -> str:
        """양자화한 메시의 해시(플랫폼 비교용)."""
        h = hashlib.sha256()
        h.update(np.rint(self.verts * QUANT).astype(np.int64).tobytes())
        h.update(self.faces.astype(np.int32).tobytes())
        h.update(self.face_elem.astype(np.int32).tobytes())
        h.update("\n".join(f"{g}|{c}" for g, c in self.elems).encode("utf-8"))
        return h.hexdigest()


def extract(path: str, threads: int = 1) -> Mesh:
    import ifcopenshell
    import ifcopenshell.geom as geom

    f = ifcopenshell.open(path)
    settings = geom.settings()
    settings.set("use-world-coords", True)
    it = geom.iterator(settings, f, threads)
    per: dict[str, tuple[str, np.ndarray, np.ndarray]] = {}
    if it.initialize():
        while True:
            sh = it.get()
            prod = f.by_id(sh.id)
            cls = prod.is_a()
            if not any(prod.is_a(c) for c in SKIP_CLASSES):
                v = np.array(sh.geometry.verts, dtype=np.float64).reshape(-1, 3)
                t = np.array(sh.geometry.faces, dtype=np.int64).reshape(-1, 3)
                if len(t):
                    per[prod.GlobalId] = (cls, v, t)
            if not it.next():
                break
    verts, faces, fe, elems = [], [], [], []
    base = 0
    for gid in sorted(per):
        cls, v, t = per[gid]
        verts.append(np.rint(v * QUANT) / QUANT)
        faces.append((t + base).astype(np.int32))
        fe.append(np.full(len(t), len(elems), np.int32))
        elems.append((gid, cls))
        base += len(v)
    if not verts:
        return Mesh(np.zeros((0, 3)), np.zeros((0, 3), np.int32), np.zeros(0, np.int32), [])
    return Mesh(np.concatenate(verts), np.concatenate(faces), np.concatenate(fe), elems)
