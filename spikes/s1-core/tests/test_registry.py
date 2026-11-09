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
    assert [i["name"] for i in r["result"]["items"]] == ["ifc.export", "ifc.restore", "view.export"]   # view.export: 덮어쓸 수 있는 파생 파일(S3 검수 4)
    r = dispatch.run(env, "commands.describe", {"name": "wall.move"})
    assert r["result"]["input_schema"]["properties"]["dx"] and r["result"]["examples"]
    r = dispatch.run(env, "commands.describe", {"name": "wall.mov"})
    assert r["error"]["code"] == "UNKNOWN_COMMAND"
