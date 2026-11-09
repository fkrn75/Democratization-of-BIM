# SPDX-License-Identifier: MPL-2.0
"""표준 라이브러리만으로 PNG(8비트 RGB) 쓰기. 필터 없음(0) + zlib. 같은 픽셀이면 같은 zlib 구현에서 같은 바이트."""
from __future__ import annotations

import struct
import zlib

import numpy as np


def _chunk(tag: bytes, data: bytes) -> bytes:
    body = tag + data
    return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)


def encode_rgb(img: np.ndarray, level: int = 6) -> bytes:
    """img: (H, W, 3) uint8."""
    if img.dtype != np.uint8 or img.ndim != 3 or img.shape[2] != 3:
        raise ValueError("(H, W, 3) uint8 이 필요하다")
    h, w, _ = img.shape
    raw = np.concatenate([np.zeros((h, 1), np.uint8), img.reshape(h, w * 3)], axis=1).tobytes()
    ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", ihdr) + _chunk(b"IDAT", zlib.compress(raw, level)) + _chunk(b"IEND", b"")


def decode_rgb(data: bytes) -> np.ndarray:
    """encode_rgb 가 쓴 PNG(필터 0)만 읽는다(시험용)."""
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    pos, idat, w = 8, b"", 0
    h = 0
    while pos < len(data):
        n = struct.unpack(">I", data[pos:pos + 4])[0]
        tag = data[pos + 4:pos + 8]
        body = data[pos + 8:pos + 8 + n]
        if tag == b"IHDR":
            w, h = struct.unpack(">II", body[:8])
        elif tag == b"IDAT":
            idat += body
        pos += 12 + n
    raw = np.frombuffer(zlib.decompress(idat), np.uint8).reshape(h, 1 + w * 3)
    assert (raw[:, 0] == 0).all()
    return raw[:, 1:].reshape(h, w, 3).copy()
