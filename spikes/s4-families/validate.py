# SPDX-License-Identifier: MPL-2.0
"""패밀리 레시피 검증기 한 줄 실행기 (S4).

사용:  <venv python> validate.py <레시피.json> [<레시피2.json> ...] [--level static|full] [--out 결과.json]
출력:  JSON 한 덩어리(stdout, UTF-8). 레시피마다 ok/checks/errors(경로·이유·힌트)/warnings/stats, 마지막에 통과 개수.
종료코드: 모두 통과 0, 하나라도 실패 1, 사용법 오류 2.
검증 내용은 SPEC.md 의 "검증기" 절을 보라(스키마·식·참조·범위·형상·IFC 유효성·표준 속성셋·실제 메시).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "s1-core" / "src"))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="패밀리 레시피 검증")
    ap.add_argument("recipes", nargs="+", help="레시피 JSON 파일")
    ap.add_argument("--level", choices=["static", "full"], default="full")
    ap.add_argument("--out", help="결과를 이 파일에도 쓴다")
    a = ap.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    from dobim_core.family.recipe import FamilyError, load_json_text
    from dobim_core.family.validate import validate_recipe
    results = []
    for name in a.recipes:
        p = Path(name)
        try:
            data = load_json_text(p.read_text(encoding="utf-8"))
        except FileNotFoundError:
            res = {"ok": False, "total_errors": 1, "errors": [{"level": "error", "code": "E_FILE", "path": "", "message": "파일이 없다", "hint": None}], "warnings": []}
        except UnicodeDecodeError:
            res = {"ok": False, "total_errors": 1, "errors": [{"level": "error", "code": "E_FILE", "path": "", "message": "UTF-8 이 아니다", "hint": None}], "warnings": []}
        except FamilyError as ex:
            res = {"ok": False, "total_errors": len(ex.issues), "errors": [i.to_dict() for i in ex.issues], "warnings": [],
                   "checks": {"schema": "fail"}}
        else:
            res = validate_recipe(data, a.level)
        res = {"file": p.name, **res}
        results.append(res)
    out = {"passed": sum(1 for r in results if r["ok"]), "total": len(results), "level": a.level, "results": results}
    text = json.dumps(out, ensure_ascii=False, indent=2)
    if a.out:
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        Path(a.out).write_text(text, encoding="utf-8")
    print(text)
    return 0 if out["passed"] == out["total"] else 1


if __name__ == "__main__":
    sys.exit(main())
