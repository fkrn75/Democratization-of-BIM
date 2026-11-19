# SPDX-License-Identifier: MPL-2.0
"""한국 납품 규칙 커맨드: rules.list(규칙 목록), rules.check(IFC 파일·납품 폴더 검사).

규칙 37개(IDS 18 + 코드 19)는 S9 스파이크에서 코어 패키지 dobim_core.krules 로 옮겼다. 프로파일은 procurement(기본,
조달청 v2.1 + 국토부 시행지침)와 seumteo(세움터 초안: 공통 12 + 세움터 15)다. 판정 결과는 S9 실행기와 같다.

이 모듈은 ifcopenshell 을 import 하지 않는다(레지스트리·`dobim list` 가 무거운 라이브러리 없이 응답해야 한다).
검사 실행기(dobim_core.krules.runner)는 커맨드가 불릴 때 지연 import 한다.
"""
from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field, StringConstraints

from dobim_core import limits
from dobim_core.commands import A, command
from dobim_core.errors import DobimError
from dobim_core.paths import resolve_in_workspace

Profile = Literal["procurement", "seumteo"]
RuleId = Annotated[str, StringConstraints(pattern=r"^KR-[A-Z]+(-[0-9]{0,3})?$", max_length=16)]
IFC_SUFFIXES = (".ifc", ".ifczip", ".ifcxml")
SHOWN_EXAMPLES = 3     # detail=true 일 때 규칙마다 보이는 위반 사례 수(전체 개수는 failed)
TEXT_CHARS = 160       # 사례·노트 문자열 길이 상한(응답 크기 상한을 지키려고 자른다)

# 실행기 입력 오류 코드 -> 봉투 오류 코드(실행기의 원래 코드는 details.input_error 에 남긴다)
_INPUT_ERROR = {"NOT_FOUND": "NOT_FOUND", "NOT_A_FILE": "PATH_DENIED", "NOT_A_DIRECTORY": "PATH_DENIED"}


def _rules():
    from dobim_core.krules import catalog, profiles
    return catalog, profiles


def _rule_rows(profile: str) -> list[dict]:
    """프로파일의 규칙 목록. procurement 는 37개, seumteo 는 37개(조달청 전용은 in_profile=false) + 세움터 15개."""
    catalog, profiles = _rules()
    from dobim_core.krules import runner, seumteo
    from dobim_core.krules.codechecks import CODE_CHECKS
    rows = []
    for cid, (mode, sev) in catalog.CHECKS.items():
        scope = catalog.CHECK_SCOPE[cid]
        title = runner._ids_title(cid) if mode == "ids" else runner._check_title(cid, CODE_CHECKS.get(cid))
        row = {"id": cid, "mode": mode, "severity": sev, "scope": scope, "title": title}
        if profile == "seumteo":
            row["in_profile"] = scope != "procurement"
            if cid in profiles.REPLACED_BY:
                row["replaced_by"] = profiles.REPLACED_BY[cid]
        rows.append(row)
    if profile == "seumteo":
        fns = dict(seumteo.SEU_CHECKS)
        fns[seumteo.PACKAGE_CHECK[0]] = seumteo.PACKAGE_CHECK[1]
        for cid, (mode, sev, _reqs, olds) in catalog.SEUMTEO_CHECKS.items():
            row = {"id": cid, "mode": mode, "severity": sev, "scope": "seumteo", "title": runner._seu_title(fns[cid]),
                   "in_profile": True}
            if olds:
                row["replaces"] = list(olds)
            rows.append(row)
    return rows


def _page_of(rows: list, a, base: dict) -> dict:
    """rows 를 커서(시작 위치)부터 limit 개, 응답 크기 상한 안에서 담는다."""
    start = limits.dec_cursor(a.cursor, "int") or 0
    if start > len(rows):
        raise DobimError("SCHEMA_INVALID", "커서가 목록 범위를 벗어났다", path="/cursor")
    chunk = rows[start:start + a.limit]
    items, cut = limits.fit(chunk, base, cursor_of=lambda i: limits.enc_cursor(start + i + 1))
    nxt = start + len(items)
    return {**base, "items": items, "page": limits.page(limits.enc_cursor(nxt) if nxt < len(rows) else None, len(rows), cut)}


def _matches(cid: str, pats) -> bool:
    return not pats or any(cid == p or (cid.startswith(p) and p.endswith("-")) for p in pats)


def _check_patterns(pats, known: list[str]):
    """규칙 id·접두가 하나도 맞지 않으면 오타일 가능성이 커서 NOT_FOUND 로 알린다(조용히 빈 결과를 내지 않는다)."""
    for i, p in enumerate(pats or ()):
        if not any(_matches(cid, [p]) for cid in known):
            raise DobimError("NOT_FOUND", f"규칙이 없다: {p}", path=f"/rules/{i}",
                             hint="rules.list 로 규칙 id 를 확인하세요. 접두로 거를 때는 KR-IDS- 처럼 '-' 로 끝내세요")


class RulesList(A):
    profile: Profile = Field("procurement", description="납품처 프로파일: procurement(조달청+국토부, 기본) 또는 seumteo(세움터 초안)")
    mode: Literal["ids", "code"] | None = Field(None, description="IDS 규칙만(ids) 또는 코드 검사만(code). 생략하면 전부")
    limit: int = Field(100, ge=1, le=200, description="최대 개수. 응답 크기 상한에 걸리면 이보다 적게 돌려주고 page.truncated 로 알린다")
    cursor: str | None = Field(None, max_length=64, description="이전 응답의 page.next_cursor")


@command("rules.list", kind="query", risk="read", group="rules", summary="한국 납품 규칙 목록(규칙 id·방식·심각도·소속·제목)",
         args=RulesList, needs_project=False, idempotent=True, since="0.2",
         examples=[{}, {"profile": "seumteo", "mode": "code"}])
def rules_list(env, a: RulesList):
    rows = [r for r in _rule_rows(a.profile) if a.mode is None or r["mode"] == a.mode]
    catalog, profiles = _rules()
    base = {"profile": a.profile, "counts": {"ids": sum(1 for m, _ in catalog.CHECKS.values() if m == "ids"),
                                             "code": sum(1 for m, _ in catalog.CHECKS.values() if m == "code"),
                                             **({"seumteo": len(catalog.SEUMTEO_CHECKS)} if a.profile == "seumteo" else {})}}
    if a.profile == "seumteo":
        base["draft_warning"] = profiles.DRAFT_WARNING
    return _page_of(rows, a, base)


class RulesCheck(A):
    path: str | None = Field(None, max_length=260, description="작업 폴더 안의 검사할 IFC 파일(.ifc·.ifczip·.ifcxml). 읽기만 한다. 생략하면 package 만 검사")
    profile: Profile = Field("procurement", description="납품처 프로파일: procurement(조달청 v2.1 + 국토부 시행지침, 기본) 또는 seumteo(세움터 초안)")
    rules: list[RuleId] | None = Field(None, max_length=60, description="보일 규칙 id 또는 접두(예: KR-IDS-01, KR-CODE-). 판정은 늘 전부 돌리고 결과만 거른다. violations·종료 코드도 거른 규칙 기준")
    ids: str | None = Field(None, max_length=260, description="작업 폴더 안의 사용자 IDS(.ids). 주면 기본 IDS 대신 이 파일의 명세를 그대로 판정한다(한국어 사용자 속성 OR 판정을 쓰지 않음). identifier 가 KR-IDS-nn 이 아닌 명세는 KR-USR-n 으로 판정한다. procurement 전용")
    energy: bool = Field(False, description="에너지 검토(환경 시뮬레이션) 사업: 조건부 규칙 KR-IDS-08/09/10 도 판정한다")
    deliverable: bool = Field(False, description="납품 후보: 파일명 규칙(KR-CODE-02/03)도 판정한다")
    as_name: str | None = Field(None, max_length=120, description="파일명 규칙을 이 이름으로 판정한다(예: 00_본관_BA.ifc)")
    package: str | None = Field(None, max_length=260, description="작업 폴더 안의 납품 폴더: 폴더 구성 규칙(KR-CODE-18, 세움터는 KR-SEU-02)도 검사한다")
    detail: bool = Field(False, description="true 면 규칙마다 제목·심각도·노트·위반 사례(앞 3건)를 함께 준다")
    limit: int = Field(100, ge=1, le=200, description="결과 행 최대 개수. 응답 크기 상한에 걸리면 이보다 적게 돌려주고 page.truncated 로 알린다")
    cursor: str | None = Field(None, max_length=64, description="이전 응답의 page.next_cursor")


def _clip(s):
    return limits.clip_chars(s, TEXT_CHARS) if isinstance(s, str) else s


def _row(c: dict, detail: bool) -> dict:
    r = {"id": c["id"], "status": c["status"], "applicable": c.get("applicable", 0), "failed": c.get("failed", 0)}
    if detail:
        r.update(title=c.get("title"), mode=c.get("mode"), severity=c.get("severity"), note=_clip(c.get("note", "")),
                 examples=[{k: _clip(v) for k, v in ex.items() if v is not None} for ex in c.get("examples", [])[:SHOWN_EXAMPLES]])
        for k in ("scope", "replaced_by", "replaces", "conditional_off"):
            if k in c:
                r[k] = c[k]
    return r


def _input_error(e, where: str):
    code = _INPUT_ERROR.get(e.code, "IMPORT_INVALID")
    return DobimError(code, f"검사할 입력을 읽을 수 없다: {e.message}", path=where, details={"input_error": e.code})


@command("rules.check", kind="query", risk="read", group="rules",
         summary="한국 납품 규칙(IDS 18 + 코드 19, 프로파일 procurement·seumteo)으로 IFC 파일·납품 폴더를 검사한다. 위반이 있으면 CLI 종료 코드 3",
         args=RulesCheck, needs_project=False, idempotent=True, since="0.2",
         examples=[{"path": "export-example.ifc"},
                   {"path": "export-example.ifc", "profile": "seumteo", "rules": ["KR-SEU-"], "detail": True, "limit": 3}])
def rules_check(env, a: RulesCheck):
    from dobim_core.krules import ids_eval, runner
    if a.path is None and a.package is None:
        raise DobimError("USAGE", "path(IFC 파일)나 package(납품 폴더) 중 하나는 필요하다", path="/path")
    if a.ids is not None and a.profile != "procurement":
        raise DobimError("USAGE", "사용자 IDS(ids)는 procurement 프로파일에서만 쓴다", path="/ids")
    src = pkg = ids_path = None
    if a.path is not None:
        src = resolve_in_workspace(env.workspace, a.path, must_exist=True)
        if src.suffix.lower() not in IFC_SUFFIXES:
            raise DobimError("PATH_DENIED", f"검사할 파일은 .ifc·.ifczip·.ifcxml 이어야 한다: {src.name}", path="/path")
    if a.package is not None:
        pkg = resolve_in_workspace(env.workspace, a.package, must_exist=True)
        if not pkg.is_dir():
            raise DobimError("PATH_DENIED", f"납품 폴더가 아니다: {pkg.name}", path="/package")
    if a.ids is not None:
        ids_path = resolve_in_workspace(env.workspace, a.ids, must_exist=True)
        if ids_path.suffix.lower() != ".ids" or not ids_path.is_file():
            raise DobimError("PATH_DENIED", f"사용자 IDS 는 .ids 파일이어야 한다: {ids_path.name}", path="/ids")
        try:
            ids_eval.load(ids_path)   # 형식·지원 여부를 먼저 확인한다(실행 중 오류는 규칙별 error 로 묻히므로)
        except ids_eval.IdsError as e:
            raise DobimError("UNSUPPORTED" if e.code == "UNSUPPORTED" else "IMPORT_INVALID", f"사용자 IDS 를 쓸 수 없다: {e.message}",
                             path="/ids") from None
    try:
        if src is not None:
            kw = {"ids_path": ids_path} if ids_path is not None else {}
            res = runner.run_file(src, a.deliverable, a.as_name, energy=a.energy, package=pkg, profile=a.profile, **kw)
        else:
            res = runner.run_package(pkg, profile=a.profile)
    except runner.InputError as e:
        raise _input_error(e, "/path" if src is not None else "/package") from None
    checks = res["checks"]
    _check_patterns(a.rules, [c["id"] for c in checks])
    shown = [c for c in checks if _matches(c["id"], a.rules)]
    base = {"file": res.get("file"), "package": pkg.name if pkg is not None else None, "schema": res.get("schema"),
            "profile": a.profile, "profile_label": res["profile"],
            "energy_mode": res.get("energy_mode", False), "deliverable_mode": res.get("deliverable_mode", False),
            "name_checked_as": res.get("name_checked_as"), "rules": len(checks), "summary": res["summary"],
            "violations": [c["id"] for c in shown if c["status"] == "fail"],
            "errors": [c["id"] for c in shown if c["status"] == "error"]}
    if "draft_warning" in res:
        base["draft_warning"] = res["draft_warning"]
    if "parse_errors" in res:
        base["parse_errors"] = res["parse_errors"]["count"]
    return _page_of([_row(c, a.detail) for c in shown], a, base)
