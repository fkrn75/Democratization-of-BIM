# SPDX-License-Identifier: MPL-2.0
"""7차 검수 후속 회귀: (1) 권한 거부(ACL)가 FILE_LOCKED(재시도 가능)가 아니라 PATH_DENIED 로 보고된다,
(2) 오류 봉투의 details 도 응답 상한(바이트·추정 토큰)을 지킨다."""
import errno
import json
import os
import subprocess
import sys
from contextlib import contextmanager

import pytest

import golden
import kit
from dobim_core import commands, dispatch, fixture as F, limits

win_only = pytest.mark.skipif(sys.platform != "win32", reason="icacls 로 ACL 거부를 거는 시험은 윈도 전용")


# ---------------------------------------------------------------- 1. 권한 거부 분류
def _icacls(*args) -> subprocess.CompletedProcess:
    return subprocess.run(["icacls", *map(str, args)], capture_output=True)


@contextmanager
def deny(path, perms):
    """현재 사용자에게 perms 를 거부하는 ACE 를 건다. 빠져나올 때(예외 포함) 반드시 걷어 낸다: 남으면 tmp_path 를 지우지 못한다."""
    user = os.environ["USERNAME"]
    r = _icacls(path, "/deny", f"{user}:({perms})")
    assert r.returncode == 0, r.stderr
    try:
        yield
    finally:
        _icacls(path, "/remove:d", user)


def _export(s, path):
    r = s.run("ifc.export", {"path": path})
    if not r["ok"] and r["error"]["code"] == "CONFIRM_REQUIRED":
        r = s.run("ifc.export", {"path": path}, confirm=r["error"]["details"]["confirm_token"])
    return r


def _restore(s, path):
    r = s.run("ifc.restore", {"path": path, "snapshot": 1})
    if not r["ok"] and r["error"]["code"] == "CONFIRM_REQUIRED":
        r = s.run("ifc.restore", {"path": path, "snapshot": 1}, confirm=r["error"]["details"]["confirm_token"])
    return r


def _leftovers(work):
    """판별용 임시 파일(.dobim-probe-*)과 .tmp 가 어디에도 남지 않았는지."""
    return sorted(str(p.relative_to(work)) for p in work.rglob("*") if p.name.endswith(".tmp") or p.name.startswith(".dobim-probe-"))


def _denied(r):
    assert not r["ok"], r
    assert r["error"]["code"] == "PATH_DENIED" and r["error"]["retryable"] is False, r["error"]


@pytest.fixture
def proj(tmp_path):
    s = kit.Session(tmp_path / "p.dobim")
    for n, a in F.SCRIPT:
        assert s.run(n, a)["ok"]
    yield s
    s.close()


@win_only
def test_1_하위_폴더_쓰기_거부_ACL은_PATH_DENIED이고_재시도_불가다(proj, tmp_path):
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "b.ifc").write_bytes(b"x")
    with deny(sub, "WD,AD"):      # 파일 생성·폴더 생성 거부
        _denied(_export(proj, "sub/b.ifc"))     # 기존 파일 덮어쓰기(스냅숏 폴더를 못 만든다)
        _denied(_export(proj, "sub/new.ifc"))   # 새 파일
    assert (sub / "b.ifc").read_bytes() == b"x"          # 원본 불변
    assert _leftovers(tmp_path) == []
    assert not (sub / "new.ifc").exists()


@win_only
def test_1_파일_자체의_쓰기_삭제_거부_ACL은_export_restore_모두_PATH_DENIED다(proj, tmp_path):
    assert _export(proj, "c.ifc")["ok"] and _export(proj, "c.ifc")["ok"]   # 스냅숏 1건 이상 확보
    before = (tmp_path / "c.ifc").read_bytes()
    with deny(tmp_path / "c.ifc", "W,D,DC"):
        _denied(_export(proj, "c.ifc"))
        _denied(_restore(proj, "c.ifc"))
    assert (tmp_path / "c.ifc").read_bytes() == before
    assert _leftovers(tmp_path) == []
    assert _export(proj, "c.ifc")["ok"]       # 거부를 걷으면 다시 쓸 수 있다


@win_only
@pytest.mark.parametrize("perms", ["D", "W"])
def test_1_삭제만_또는_쓰기만_거부해도_덮어쓸_수_없으면_PATH_DENIED다(proj, tmp_path, perms):
    """이름 바꾸기로 덮어쓰려면 대상의 쓰기와 삭제 권한이 모두 필요하다: 둘 중 하나만 막혀도 재시도로 풀리지 않는다."""
    assert _export(proj, "e.ifc")["ok"] and _export(proj, "e.ifc")["ok"]
    before = (tmp_path / "e.ifc").read_bytes()
    with deny(tmp_path / "e.ifc", perms):
        _denied(_export(proj, "e.ifc"))
    assert (tmp_path / "e.ifc").read_bytes() == before and _leftovers(tmp_path) == []


@win_only
def test_1_ACL_거부_뒤에도_스냅숏이_쌓이지_않는다(proj, tmp_path):
    assert _export(proj, "d.ifc")["ok"] and _export(proj, "d.ifc")["ok"]   # 두 번째부터 스냅숏이 생긴다
    snaps = lambda: sorted(p.name for p in (tmp_path / ".dobim-snapshots").iterdir())   # noqa: E731
    base = snaps()
    with deny(tmp_path / "d.ifc", "W,D,DC"):
        for _ in range(3):
            _denied(_export(proj, "d.ifc"))
    assert snaps() == base


@win_only
def test_1_다른_프로세스가_잡은_파일은_여전히_FILE_LOCKED다(tmp_path):
    """기존 잠금 시험(공유 없이 잡은 .tmp, 삭제 공유 없이 잡은 대상)이 그대로 통과한다."""
    assert golden.check_file_lock(tmp_path) == []


def test_1_분류표_접근_거부는_증거에_따라_갈린다(tmp_path, monkeypatch):
    """윈도가 아니어도 도는 단위 시험: _access_state 가 denied 면 PATH_DENIED, busy·ok 면 FILE_LOCKED."""
    p = tmp_path / "o.ifc"
    p.write_bytes(b"x")
    ex = PermissionError(errno.EACCES, "x")
    for state, code in (("denied", "PATH_DENIED"), ("busy", "FILE_LOCKED"), ("ok", "FILE_LOCKED")):
        monkeypatch.setattr(commands, "_access_state", lambda *_a, s=state, **_k: s)
        e = commands._io_error(p, ex)
        assert e.code == code and (e.to_dict()["retryable"] is (code == "FILE_LOCKED"))
    monkeypatch.undo()
    assert commands._io_error(p, ex).code == "FILE_LOCKED"        # 쓸 수 있는 파일의 접근 거부는 기존대로 잠금으로 본다


def test_1_판별용_probe가_파일을_남기지_않는다(tmp_path):
    p = tmp_path / "o.ifc"
    p.write_bytes(b"x")
    assert commands._access_state(p, PermissionError(errno.EACCES, "x")) == "ok"
    assert [x.name for x in tmp_path.iterdir()] == ["o.ifc"] and p.read_bytes() == b"x"


# ---------------------------------------------------------------- 2. 오류 봉투 크기
def _within(r):
    b = len(json.dumps(r, ensure_ascii=False).encode("utf-8"))
    t = limits.est_tokens(r)
    assert b <= limits.RESPONSE_CAP_BYTES and t <= limits.RESPONSE_CAP_TOKENS, (b, t)
    return b


@pytest.mark.parametrize("ch", ["k", "가", "😀", "\x01", '"'])
def test_2_거대한_키가_든_SCHEMA_INVALID도_상한_안이다(base, ch):
    r = base.run("wall.move", {"id": F.W1, ch * 100_000: 1})
    assert not r["ok"] and r["error"]["code"] == "SCHEMA_INVALID"
    _within(r)
    d = r["error"]["details"]
    assert d["errors"] and all(len(e["path"]) <= limits.DETAIL_PATH_CHARS for e in d["errors"])
    assert d["total"] == 1 and d["truncated"] is False
    assert r["error"]["path"] and r["error"]["retryable"] is False


def test_2_오류가_많으면_개수_상한과_총_개수가_표시된다(base):
    extra = {f"{'가' * 200}{i}": i for i in range(300)}
    r = base.run("wall.move", {"id": F.W1, "dx": 1, **extra})
    assert r["error"]["code"] == "SCHEMA_INVALID"
    _within(r)
    d = r["error"]["details"]
    assert d["total"] == 300 and d["truncated"] is True
    assert 1 <= len(d["errors"]) <= limits.MAX_DETAIL_ERRORS
    assert all(e["path"].endswith("…") for e in d["errors"])


def test_2_정상_크기의_오류는_바뀌지_않는다(base):
    r = base.run("wall.move", {"id": F.W1, "dz": 1})
    d = r["error"]["details"]
    assert d["total"] == len(d["errors"]) == 1 and d["truncated"] is False
    assert d["errors"][0]["path"] == "/dz" and not d["errors"][0]["path"].endswith("…")
    assert limits.fit_error(r["error"]) is r["error"]       # 이미 상한 안이면 그대로(복사도 하지 않는다)


def test_2_거대한_id_값_커맨드_이름_배치_script_실패도_상한_안이다(base):
    huge = "가" * 100_000
    _within(base.run("wall.move", {"id": huge, "dx": 1}))
    _within(base.run(huge))
    r = base.batch([{"command": "wall.move", "args": {"id": F.W1, huge: 1}}])
    assert not r["ok"]
    _within(r)
    s = dispatch.run_script(base.env, [{"command": "wall.move", "args": {"id": F.W1, huge: 1}}], project=base.path.name)
    assert not s["ok"]
    _within(s)
    assert s["error"]["code"] == "SCHEMA_INVALID"


def test_2_script_앞쪽_성공_항목이_많아도_실패_오류까지_상한_안이다(base):
    huge = "😀" * 50_000
    items = [{"command": "wall.move", "args": {"id": F.W1, "dx": 1}} for _ in range(40)]
    items.append({"command": "wall.move", "args": {"id": F.W1, huge: 1}})
    s = dispatch.run_script(base.env, items, project=base.path.name)
    assert not s["ok"] and s["error"]["code"] == "SCHEMA_INVALID"
    _within(s)


def test_2_fit_error는_토큰_확인_토큰과_코드를_자르지_않는다():
    err = {"code": "CONFIRM_REQUIRED", "message": "가" * 400, "path": "/confirm", "retryable": False, "hint": "나" * 400,
           "details": {"preview": {"will_write": "😀" * 255}, "confirm_token": "1700000000." + "a" * 24, "expires_in_s": 300, "rev": 3}}
    out = limits.fit_error(err)
    assert out["code"] == "CONFIRM_REQUIRED" and out["details"]["confirm_token"] == err["details"]["confirm_token"]
    _within({"ok": False, "error": out, "rev": 3})
    tiny = limits.fit_error(err, 700, 300)      # 아주 작은 상한에서도 모양과 토큰은 유지한다
    assert tiny["details"]["confirm_token"] == err["details"]["confirm_token"] and tiny["code"] == "CONFIRM_REQUIRED"
