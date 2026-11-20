# SPDX-License-Identifier: MPL-2.0
import json
import subprocess
import sys

from dobim_core import dispatch, registry
from conftest import ROOT


def test_모든_커맨드가_예시와_스키마를_가진다():
    cmds = registry.load()
    assert len(cmds) >= 25
    for name, c in cmds.items():
        assert c.examples, name
        d = c.describe()
        json.dumps(d)                                  # JSON Schema 직렬화 가능
        assert d["input_schema"]["type"] == "object"
        for ex in c.examples:
            c.args_model.model_validate(ex)             # 예시는 자기 스키마를 통과한다
        # 조회는 read, 변경은 write: 위험 등급이 종류와 어긋나면 승인 정책이 무력해진다
        if c.kind == "query":
            assert c.risk == "read", name
        if c.kind == "mutation":
            assert c.risk == "write", name


def test_레지스트리_로드는_ifcopenshell을_가져오지_않는다():
    code = ("import sys; from dobim_core import registry, dispatch, mcp_server; registry.load(); "
            "mcp_server.build(dispatch.Env('.')); print('ifcopenshell' in sys.modules)")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, cwd=ROOT, env={"PYTHONPATH": str(ROOT / "src"),
                         "PATH": "", "SYSTEMROOT": __import__("os").environ.get("SYSTEMROOT", "")}, timeout=60)
    assert out.stdout.decode().strip() == "False", out.stderr.decode()[-300:]


def test_검색과_설명(tmp_path):
    env = dispatch.Env(tmp_path)
    r = dispatch.run(env, "commands.search", {"query": "wall"})
    assert {i["name"] for i in r["result"]["items"]} >= {"wall.create", "wall.move", "wall.set"}
    r = dispatch.run(env, "commands.search", {"risk": "destructive"})
    assert [i["name"] for i in r["result"]["items"]] == ["ifc.export", "ifc.restore", "schedule.export", "sheet.export", "sheet.index", "sheet.render", "sheet.render_set", "view.export"]   # view.export·sheet.export: 덮어쓸 수 있는 파생 파일(S3 검수 4), schedule.export: 일람표 CSV·시트(W1)
    r = dispatch.run(env, "commands.describe", {"name": "wall.move"})
    assert r["result"]["input_schema"]["properties"]["dx"] and r["result"]["examples"]
    r = dispatch.run(env, "commands.describe", {"name": "wall.mov"})
    assert r["error"]["code"] == "UNKNOWN_COMMAND"


def _both_copies(env_out: dict) -> str:
    """MCP 는 같은 봉투를 text 와 structuredContent 에 한 벌씩 싣는다: 두 벌을 이어 잰다(s11 size_probe 와 같은 측정)."""
    t = json.dumps(env_out, ensure_ascii=False)
    return t + t


def test_모든_커맨드_describe_응답이_8000토큰_이내다(tmp_path):
    """S11 크기 기준(도구 응답 8,000토큰 이내)을 describe 전체에 건다: 기본 응답과 나눈 부분(section) 하나하나.
    바이트/3 추정은 항상, tiktoken(개발 의존)이 있으면 cl100k·o200k 실측도 본다. 새 커맨드·긴 설명이 생겨도 나눔이 자동으로 따라오는지 지킨다."""
    try:
        import tiktoken
        encs = [tiktoken.get_encoding(n) for n in ("cl100k_base", "o200k_base")]
    except ImportError:
        encs = []
    env = dispatch.Env(tmp_path)
    over = []
    for name in sorted(registry.load()):
        r = dispatch.run(env, "commands.describe", {"name": name})
        assert r["ok"], name
        outs = [(None, r)] + [(s, dispatch.run(env, "commands.describe", {"name": name, "section": s}))
                              for s in r["result"].get("split", {}).get("sections", [])]
        for sec, e in outs:
            assert e["ok"], (name, sec, e.get("error"))
            both = _both_copies(e)
            sizes = [len(both.encode("utf-8")) / 3] + [len(enc.encode(both, disallowed_special=())) for enc in encs]
            if max(sizes) > 8000:
                over.append((name, sec, max(sizes)))
    assert not over, over


def test_describe_나눔은_손실이_없고_작은_커맨드는_그대로다(tmp_path):
    from dobim_core import commands as C
    env = dispatch.Env(tmp_path)
    split = []
    for name, c in sorted(registry.load().items()):
        full = c.describe()
        head = dispatch.run(env, "commands.describe", {"name": name})["result"]
        if "split" not in head:
            assert head == json.loads(json.dumps(full)), name          # 기준 안의 커맨드는 이전 응답과 같다(하위 호환)
            continue
        split.append(name)
        assert len(json.dumps(head, ensure_ascii=False).encode("utf-8")) <= C.DESCRIBE_MAX_BYTES, name
        # 요약 + 부분을 다시 모으면 전체 스키마와 같다
        sch = json.loads(json.dumps(head["input_schema"]))
        for sec in head["split"]["sections"]:
            part = dispatch.run(env, "commands.describe", {"name": name, "section": sec})["result"]
            kind, _, key = sec.partition(":")
            if kind == "def":
                sch.setdefault("$defs", {})[key] = part["schema"]
            else:
                assert sch["properties"][key]["section"] == sec
                sch["properties"][key] = part["schema"]
                assert part["required"] == (key in full["input_schema"].get("required", []))
        assert json.dumps(sch, sort_keys=True) == json.dumps(json.loads(json.dumps(full["input_schema"])), sort_keys=True), name
        assert {k: v for k, v in head.items() if k not in ("input_schema", "split")} == json.loads(json.dumps(
            {k: v for k, v in full.items() if k != "input_schema"})), name
    assert {"solid.create", "sheet.export", "sheet.image"} <= set(split)   # 8,000토큰을 넘던 세 커맨드(C-4 기준값, W1 게이트 U-3)
    r = dispatch.run(env, "commands.describe", {"name": "solid.create", "section": "def:없음"})
    assert r["error"]["code"] == "NOT_FOUND" and "def:MemberSection" in r["error"]["hint"]
    r = dispatch.run(env, "commands.describe", {"name": "wall.move", "section": "def:X"})
    assert r["error"]["code"] == "NOT_FOUND"
