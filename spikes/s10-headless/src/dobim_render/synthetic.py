# SPDX-License-Identifier: MPL-2.0
"""고정 합성 메시: OCCT·IFC 와 무관하게 래스터라이저만의 결정성을 시험하는 데 쓴다."""
from __future__ import annotations

import numpy as np


def cube_mesh():
    """한 변 3 m 정육면체(12삼각형). 반환: (verts, faces, face_elem)."""
    v = np.array([[x, y, z] for z in (0, 1) for y in (0, 1) for x in (0, 1)], float) * 3.0
    f = np.array([[0, 1, 3], [0, 3, 2], [4, 6, 7], [4, 7, 5], [0, 4, 5], [0, 5, 1],
                  [2, 3, 7], [2, 7, 6], [0, 2, 6], [0, 6, 4], [1, 5, 7], [1, 7, 3]], np.int32)
    return v, f, np.zeros(12, np.int32)
