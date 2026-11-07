# SPDX-License-Identifier: MPL-2.0
"""합격 기준 '분기 금지': 코어·프로토콜 계층에 클라이언트(Claude, Codex)별 분기가 없는가.

두 단계로 본다.
1. 어휘 검사: 소스에 클라이언트 이름·식별 필드(claude, codex, anthropic, openai, clientInfo, client_info, user-agent)가 있는가.
   주석·문서 문자열도 센다(분기를 설명하는 주석도 흔적이므로 보고 대상이다).
2. 구조 검사(AST): mcp_server / dispatch 에서 MCP 서버 컨텍스트의 client_params 나 session.client_params 를 읽는 코드가 있는가.
   클라이언트가 누구인지 알아내는 유일한 정식 경로가 그것이기 때문이다.
음성 대조: 같은 검사를 분기가 든 가짜 소스에 돌려 검사기가 실제로 잡아내는지 증명한다.
"""
from __future__ import annotations

import ast
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC = HERE.parent.parent / "s1-core" / "src" / "dobim_core"
VOCAB = re.compile(r"claude|codex|anthropic|openai|clientinfo|client_info|client_params|user[-_ ]?agent|gpt-", re.I)
ATTRS = {"client_params", "clientInfo", "client_info"}


def _docstring_nodes(tree) -> set[int]:
    ids = set()
    for n in ast.walk(tree):
        if isinstance(n, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and n.body:
            f = n.body[0]
            if isinstance(f, ast.Expr) and isinstance(f.value, ast.Constant) and isinstance(f.value.value, str):
                ids.add(id(f.value))
    return ids


def scan_source(name: str, text: str) -> list[dict]:
    """코드(식별자·문자열 리터럴·속성)에서 찾은 것만 분기 후보로 센다. 주석·문서 문자열의 언급은 kind=설명 으로 따로 둔다."""
    hits = []
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return [{"file": name, "line": 0, "kind": "구문오류", "match": ""}]
    docs = _docstring_nodes(tree)
    for node in ast.walk(tree):
        s = None
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            s = node.value
            kind = "설명(문서 문자열)" if id(node) in docs else "코드(문자열)"
        elif isinstance(node, ast.Name):
            s, kind = node.id, "코드(이름)"
        elif isinstance(node, ast.Attribute):
            s, kind = node.attr, "코드(속성)"
            if node.attr in ATTRS:
                hits.append({"file": name, "line": node.lineno, "kind": "구조", "match": node.attr})
        elif isinstance(node, ast.arg):
            s, kind = node.arg, "코드(인자)"
        if s is not None:
            m = VOCAB.search(s)
            if m:
                hits.append({"file": name, "line": getattr(node, "lineno", 0), "kind": kind, "match": m.group(0)})
    for i, line in enumerate(text.splitlines(), 1):
        if "#" in line:
            m = VOCAB.search(line.split("#", 1)[1])
            if m:
                hits.append({"file": name, "line": i, "kind": "설명(주석)", "match": m.group(0)})
    return hits


def scan_dir(root: Path) -> tuple[list[dict], int]:
    files = sorted(root.glob("*.py"))
    hits = []
    for f in files:
        hits += scan_source(f.name, f.read_text(encoding="utf-8"))
    return hits, len(files)


NEGATIVE_SAMPLES = {
    "어휘": "def tool(x):\n    if 'codex' in x:\n        return 1\n",
    "구조": "def tool(ctx):\n    return ctx.session.client_params.clientInfo.name\n",
}


def main(out: str | None) -> int:
    allhits, nfiles = scan_dir(SRC)
    hits = [h for h in allhits if not h["kind"].startswith("설명")]
    notes = [h for h in allhits if h["kind"].startswith("설명")]
    neg = {k: bool(scan_source("neg.py", v)) for k, v in NEGATIVE_SAMPLES.items()}
    res = {"scanned_files": nfiles, "scanned_dir": "spikes/s1-core/src/dobim_core",
           "branch_hits": len(hits), "hits": hits, "mentions_in_docs_or_comments": notes, "negative_control_detected": neg,
           "pass": not hits and all(neg.values())}
    text = json.dumps(res, ensure_ascii=False, indent=2)
    if out:
        Path(out).write_text(text, encoding="utf-8")
    print(text)
    return 0 if res["pass"] else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else None))
