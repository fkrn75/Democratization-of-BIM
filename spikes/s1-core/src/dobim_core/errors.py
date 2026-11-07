# SPDX-License-Identifier: MPL-2.0
"""구조화된 오류와 CLI 종료 코드 (architecture.md 15.3, 15.5)."""
from __future__ import annotations

# 오류 코드 → 종료 코드. 표에 없는 코드를 쓰면 생성 시점에 실패한다.
EXIT_CODES: dict[str, int] = {
    "CONSTRAINT": 1,
    "NOTHING_TO_UNDO": 1,
    "NOTHING_TO_REDO": 1,
    "FILE_EXISTS": 1,
    "FILE_LOCKED": 1,
    "NO_PROJECT": 2,
    "USAGE": 2,
    "UNSUPPORTED": 2,
    "UNKNOWN_COMMAND": 2,
    "SCHEMA_INVALID": 2,
    "REV_CONFLICT": 4,
    "LOCKED": 4,
    "NOT_FOUND": 5,
    "PATH_DENIED": 6,
    "CONFIRM_REQUIRED": 6,
    "CONFIRM_INVALID": 6,
    "RISK_MISMATCH": 6,
    "JOB_TIMEOUT": 7,
    "REPLAY_DIVERGED": 70,
    "ENGINE_TAINTED": 70,   # IFC 투영 복원에 실패해 로그에서 다시 만들지도 못한 상태: 쓰기·조회를 거부한다
    "INTERNAL": 70,
}

RETRYABLE = {"REV_CONFLICT", "LOCKED", "FILE_LOCKED"}


class DobimError(Exception):
    """커맨드 실패. 봉투의 error 필드로 그대로 직렬화된다."""

    def __init__(self, code: str, message: str, *, path: str | None = None,
                 hint: str | None = None, details: dict | None = None):
        super().__init__(message)
        if code not in EXIT_CODES:
            raise ValueError(f"등록되지 않은 오류 코드: {code}")
        self.code = code
        self.message = message
        self.path = path
        self.hint = hint
        self.details = details or {}

    def to_dict(self) -> dict:
        return {
            "code": self.code,
            "message": self.message,
            "path": self.path,
            "retryable": self.code in RETRYABLE,
            "hint": self.hint,
            "details": self.details,
        }


def exit_code_of(envelope: dict) -> int:
    """봉투에서 CLI 종료 코드를 구한다. 검사 위반은 성공 봉투 + 종료 코드 3이다."""
    if envelope.get("ok"):
        result = envelope.get("result")
        return 3 if isinstance(result, dict) and result.get("violations") else 0
    return EXIT_CODES.get(envelope["error"]["code"], 70)
