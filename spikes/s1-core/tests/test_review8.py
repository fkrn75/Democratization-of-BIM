# SPDX-License-Identifier: MPL-2.0
"""8차 검수 후속 회귀: (1) 오류 봉투(fit_error)가 어떤 합성 입력에서도 상한을 지킨다,
(2) 읽기 거부 ACL 은 PATH_DENIED, (3) 작업 폴더 삭제(D) 거부 때 ifcopenshell 의 RuntimeError 가 INTERNAL 로 새지 않는다,
(4) run_script 의 문자열 아닌 command 는 SCHEMA_INVALID."""
import json
import os
import subprocess
import sys
from contextlib import contextmanager

import pytest

import golden
import kit
from dobim_core import commands, dispatch, fixture as F, limits
from dobim_core.errors import DobimError

win_only = pytest.mark.skipif(sys.platform != "win32", reason="icacls 로 ACL 거부를 거는 시험은 윈도 전용")


# ---------------------------------------------------------------- 1. fit_error 상한 보장
def _fits(env, cap_b=limits.RESPONSE_CAP_BYTES, cap_t=limits.RESPONSE_CAP_TOKENS):
    b, t = limits.jsize(env), limits.est_tokens(env)
    assert b <= cap_b and t <= cap_t, (b, t)
    json.loads(json.dumps(env, ensure_ascii=False))     # 직렬화·복원이 된다


def _confirm(details, **kw):
    return DobimError("CONFIRM_REQUIRED", "x", path="/confirm", details=details, **kw)


def _cases():
    big = "가" * 300
    yield "confirm_token 20000자", _confirm({"preview": {}, "confirm_token": "1." + "a" * 20000})
    yield "confirm_token 3000자", _confirm({"preview": {}, "confirm_token": "1." + "a" * 3000})
    yield "큰 preview 문자열", _confirm({"preview": {"items": [{"k": "😀" * 200} for _ in range(500)]}, "confirm_token": "1." + "a" * 24})
    yield "깊게 중첩된 리스트", _confirm({"preview": {"deep": [[[["x" * 100] * 50] * 50] * 50]}, "confirm_token": "t"})
    yield "평탄한 큰 리스트", DobimError("REV_CONFLICT", "x", details={"expected": 1, "actual": 2, "extra": [1] * 20000})
    yield "평탄한 큰 문자열 리스트", DobimError("REV_CONFLICT", "x", details={"extra": ["가나다" * 30] * 3000})
    yield "거대한 dict 키", _confirm({"preview": {big + str(i): 1 for i in range(200)}, "confirm_token": "t"})
    yield "거대한 키 하나", _confirm({"preview": {"k" * 100_000: 1}, "confirm_token": "t"})
    yield "깊은 dict 중첩", DobimError("NOT_FOUND", "x", details={"a": {"b": {"c": {"d": {"e": {"f": {"g": {"h": big}}}}}}}})
    yield "큰 message·path·hint", DobimError("NOT_FOUND", "가" * 200_000, path="p" * 100_000, hint="😀" * 100_000)
    yield "제어문자 값", _confirm({"preview": {"v": "\x01" * 5000, "w": ['"'] * 3000}, "confirm_token": "t"})


@pytest.mark.parametrize("name,ex", list(_cases()), ids=[n for n, _ in _cases()])
def test_1_fit_error는_합성_입력에서도_상한_안이다(name, ex):
    env = dispatch.err_env(ex, 2**62)
    _fits(env)
    assert env["error"]["code"] == ex.code and env["error"]["retryable"] is (ex.code in ("REV_CONFLICT", "LOCKED", "FILE_LOCKED"))
    # 반 몫 상한(run_script 의 실패 오류)에서도 지킨다
    half = limits.fit_error(ex.to_dict(), limits.RESPONSE_CAP_BYTES // 2 - 500, limits.RESPONSE_CAP_TOKENS / 2 - 300)
    _fits({"ok": False, "error": half, "rev": 2**62}, limits.RESPONSE_CAP_BYTES // 2 - 500, limits.RESPONSE_CAP_TOKENS / 2 - 300)


def test_1_줄였으면_details_truncated로_알리고_정상_크기는_그대로다():
    out = limits.fit_error(_confirm({"preview": {"items": ["x" * 255] * 400}, "confirm_token": "1." + "a" * 24}).to_dict())
    assert out["details"]["truncated"] is True and out["details"]["confirm_token"] == "1." + "a" * 24
    small = _confirm({"preview": {"will_write": "a.ifc"}, "confirm_token": "1." + "a" * 24}).to_dict()
    assert limits.fit_error(small) is small


def test_1_말도_안_되게_긴_confirm_token은_details를_비워_상한을_지킨다():
    out = limits.fit_error(_confirm({"preview": {"a": 1}, "confirm_token": "1." + "a" * 20000}).to_dict())
    assert out["details"] == {"truncated": True} and out["code"] == "CONFIRM_REQUIRED"


def test_1_서버가_만드는_확인_토큰은_고정_길이라_상한_안이다(base):
    r = base.run("ifc.export", {"path": "t.ifc"})
    tok = r["error"]["details"]["confirm_token"]
    assert r["error"]["code"] == "CONFIRM_REQUIRED" and len(tok) <= limits.TOKEN_MAX_CHARS
    assert len(tok) < 64        # "<만료 초>.<24자>"
    assert limits.fit_error(r["error"])["details"]["confirm_token"] == tok
    # 큰 preview 가 토큰을 밀어내지 않는다: 아주 작은 상한에서도 토큰은 남는다
    tiny = limits.fit_error({**r["error"], "details": {**r["error"]["details"], "preview": {"x": "가" * 5000}}}, 900, 500)
    assert tiny["details"]["confirm_token"] == tok


# ---------------------------------------------------------------- 2·3. 권한 거부 분류
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


def _call(s, name, args):
    r = s.run(name, args)
    if not r["ok"] and r["error"]["code"] == "CONFIRM_REQUIRED":
        r = s.run(name, args, confirm=r["error"]["details"]["confirm_token"])
    return r


def _export(s, path):
    return _call(s, "ifc.export", {"path": path})


def _restore(s, path):
    return _call(s, "ifc.restore", {"path": path, "snapshot": 1})


def _leftovers(work):
    """판별용 임시 파일(.dobim-probe-*)과 .tmp 가 어디에도 남지 않았는지."""
    return sorted(str(p.relative_to(work)) for p in work.rglob("*") if p.name.endswith(".tmp") or p.name.startswith(".dobim-probe-"))


def _denied(r, word=None):
    assert not r["ok"], r
    assert r["error"]["code"] == "PATH_DENIED" and r["error"]["retryable"] is False, r["error"]
    if word:
        assert word in r["error"]["message"], r["error"]["message"]


@pytest.fixture
def proj(tmp_path):
    s = kit.Session(tmp_path / "p.dobim")
    for n, a in F.SCRIPT:
        assert s.run(n, a)["ok"]
    assert _export(s, "a.ifc")["ok"] and _export(s, "a.ifc")["ok"]      # 두 번째부터 스냅숏이 생긴다
    yield s
    s.close()


@win_only
def test_2_파일_읽기_거부는_export_restore_모두_PATH_DENIED다(proj, tmp_path):
    before = (tmp_path / "a.ifc").read_bytes()
    with deny(tmp_path / "a.ifc", "RD"):
        _denied(_export(proj, "a.ifc"), "읽을 권한")
        _denied(_restore(proj, "a.ifc"), "읽을 권한")
    assert (tmp_path / "a.ifc").read_bytes() == before and _leftovers(tmp_path) == []
    assert _export(proj, "a.ifc")["ok"]       # 거부를 걷으면 다시 쓸 수 있다


@win_only
def test_2_스냅숏_폴더_읽기_거부는_export_restore_snapshots_모두_PATH_DENIED다(proj, tmp_path):
    snap = tmp_path / ".dobim-snapshots"
    with deny(snap, "RD"):
        _denied(_export(proj, "a.ifc"), "읽을 권한")
        _denied(_restore(proj, "a.ifc"), "읽을 권한")
        _denied(proj.run("ifc.snapshots", {"path": "a.ifc"}), "읽을 권한")
    assert _leftovers(tmp_path) == []
    assert proj.run("ifc.snapshots", {"path": "a.ifc"})["ok"]


@win_only
def test_2_스냅숏_파일_읽기_거부로_복원이_실패해도_PATH_DENIED다(proj, tmp_path):
    snap = next((tmp_path / ".dobim-snapshots").iterdir())
    before = (tmp_path / "a.ifc").read_bytes()
    with deny(snap, "RD"):
        _denied(_restore(proj, "a.ifc"), "읽을 권한")
    assert (tmp_path / "a.ifc").read_bytes() == before and _leftovers(tmp_path) == []


@win_only
def test_2_쓰기_거부는_여전히_쓰기_거부로_보고한다(proj, tmp_path):
    with deny(tmp_path / "a.ifc", "W,D"):
        _denied(_export(proj, "a.ifc"), "쓸 권한")
    assert _leftovers(tmp_path) == []


@win_only
def test_2_다른_프로세스가_잡은_파일은_여전히_FILE_LOCKED다(tmp_path):
    assert golden.check_file_lock(tmp_path) == []


@win_only
def test_3_작업_폴더_삭제_거부는_RuntimeError_INTERNAL이_아니라_PATH_DENIED다(proj, tmp_path):
    before = (tmp_path / "a.ifc").read_bytes()
    snaps = sorted(p.name for p in (tmp_path / ".dobim-snapshots").iterdir())
    with deny(tmp_path, "D"):       # 폴더 안 파일의 이름 바꾸기·교체가 막힌다(만들기·지우기는 된다)
        r = _export(proj, "a.ifc")
        _denied(r, "쓸 권한")
        assert r["error"]["code"] != "INTERNAL"
        _denied(_restore(proj, "a.ifc"), "쓸 권한")
        _denied(_export(proj, "new.ifc"), "쓸 권한")        # 새 파일도 .tmp 에서 이름을 바꿔야 한다
    assert (tmp_path / "a.ifc").read_bytes() == before and not (tmp_path / "new.ifc").exists()
    assert sorted(p.name for p in (tmp_path / ".dobim-snapshots").iterdir()) == snaps     # 스냅숏이 쌓이지 않는다
    assert _leftovers(tmp_path) == []
    assert _export(proj, "a.ifc")["ok"]


# RuntimeError 분류는 윈도가 아니어도 도는 단위 시험으로 고정한다
_IFC_MSG = "Failed to write to path: 'x.ifc.tmp', could not replace the existing file."


@pytest.mark.parametrize("state,code", [("denied", "PATH_DENIED"), ("busy", "FILE_LOCKED"), ("ok", "IO_ERROR"), ("denied_read", "PATH_DENIED")])
def test_3_ifcopenshell_교체_실패는_증거에_따라_갈린다(tmp_path, monkeypatch, state, code):
    p = tmp_path / "x.ifc"
    p.write_bytes(b"old")
    monkeypatch.setattr(commands, "_access_state", lambda *_a, **_k: state)

    def write(tmp):
        raise RuntimeError(_IFC_MSG)
    with pytest.raises(DobimError) as ei:
        commands._replace(p, write)
    assert ei.value.code == code and ei.value.to_dict()["retryable"] is (code == "FILE_LOCKED")
    assert p.read_bytes() == b"old" and not (tmp_path / "x.ifc.tmp").exists()


@pytest.mark.parametrize("msg", ["something else broke", "Failed to write all bytes: 3", "could not replace the existing file."])
def test_3_다른_RuntimeError는_삼키지_않는다(tmp_path, msg):
    p = tmp_path / "x.ifc"
    p.write_bytes(b"old")

    def write(tmp):
        raise RuntimeError(msg)
    with pytest.raises(RuntimeError) as ei:
        commands._replace(p, write)
    assert not isinstance(ei.value, DobimError) and str(ei.value) == msg
    assert not (tmp_path / "x.ifc.tmp").exists()


def test_3_write_단계_밖의_RuntimeError도_삼키지_않는다(tmp_path, monkeypatch):
    p = tmp_path / "x.ifc"
    monkeypatch.setattr(type(p), "replace", lambda self, t: (_ for _ in ()).throw(RuntimeError(_IFC_MSG)), raising=False)
    with pytest.raises(RuntimeError):
        commands._replace(p, lambda tmp: tmp.write_bytes(b"n"))


# ---------------------------------------------------------------- 4. run_script 비문자열 command
@pytest.mark.parametrize("bad", [5, 3.5, ["wall.move"], {"a": 1}, True])
def test_4_run_script의_문자열_아닌_command는_SCHEMA_INVALID다(base, bad):
    items = [{"command": "model.stats", "args": {}}, {"command": bad, "args": {}}, {"command": "model.stats", "args": {}}]
    out = dispatch.run_script(base.env, items, project=base.path.name)
    assert not out["ok"] and out["error"]["code"] == "SCHEMA_INVALID", out["error"]
    assert out["error"]["path"] == "/1/command" and out["error"]["retryable"] is False
    assert "INTERNAL" not in json.dumps(out)
    assert limits.jsize(out) <= limits.RESPONSE_CAP_BYTES


def test_4_name_키가_문자열이_아니어도_SCHEMA_INVALID다(base):
    out = dispatch.run_script(base.env, [{"name": 7}], project=base.path.name)
    assert not out["ok"] and out["error"]["code"] == "SCHEMA_INVALID"


def test_4_정상_script와_없는_커맨드는_그대로다(base):
    ok = dispatch.run_script(base.env, [{"command": "model.stats", "args": {}}], project=base.path.name)
    assert ok["ok"]
    unk = dispatch.run_script(base.env, [{"command": "no.such", "args": {}}], project=base.path.name)
    assert not unk["ok"] and unk["error"]["code"] == "UNKNOWN_COMMAND"


# ---------------------------------------------------------------- 5. 클라이언트가 보낸 긴 확인 토큰은 INTERNAL 이 아니라 CONFIRM_INVALID
@pytest.mark.parametrize("tok", ["9" * 100 + ".abc", "9" * 130 + ".abc", "9" * 1000 + ".a", "9" * 5000 + ".a",
                                 "-5.a", "x" * 129])
def test_long_client_token_is_confirm_invalid(tmp_path, tok):
    s = kit.Session(tmp_path / "p.dobim")
    for n, a in F.SCRIPT:
        s.run(n, a)
    r = s.run("ifc.export", {"path": "a.ifc"}, confirm=tok)
    assert r["ok"] is False and r["error"]["code"] == "CONFIRM_INVALID", r["error"]


def test_issued_token_roundtrip_still_works(tmp_path):
    s = kit.Session(tmp_path / "p.dobim")
    for n, a in F.SCRIPT:
        s.run(n, a)
    r = s.run("ifc.export", {"path": "a.ifc"})
    tok = r["error"]["details"]["confirm_token"]
    assert len(tok) <= limits.TOKEN_MAX_CHARS
    assert s.run("ifc.export", {"path": "a.ifc"}, confirm=tok)["ok"] is True
