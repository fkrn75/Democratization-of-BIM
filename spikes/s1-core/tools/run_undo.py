# SPDX-License-Identifier: MPL-2.0
"""합격 기준 2 단독 증거: 실행 취소·다시 실행 뒤 해시 복원(분기 포함). 결과를 JSON 으로 남긴다."""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import kit  # noqa: E402

work = Path(tempfile.mkdtemp(prefix="dobim-undo-"))
runs = [kit.undo_property(seed, 200, work) for seed in range(1, 21)]
out = {"seeds": list(range(1, 21)), "items_per_seed": 200, "total_hash_verified_steps": sum(r["verified_steps"] for r in runs),
       "total_branch_checks": sum(r["branch_checks"] for r in runs), "total_txns": sum(r["txns"] for r in runs),
       "violations": [v for r in runs for v in r["violations"]], "per_seed": runs,
       "what_is_checked": "각 단계 뒤 (상태 해시, 파생값 해시, IFC 의미 해시) 세 개가 기록된 이력과 같다. 끝까지 undo -> 끝까지 redo -> 무작위 -> "
                          "undo 뒤 새 명령으로 redo 가지를 버리는 분기. 실패한 커맨드의 원자적 롤백 포함."}
out["pass"] = not out["violations"]
text = json.dumps(out, ensure_ascii=False, indent=2)
if len(sys.argv) > 1:
    Path(sys.argv[1]).write_text(text, encoding="utf-8")
print(json.dumps({k: v for k, v in out.items() if k != "per_seed"}, ensure_ascii=False, indent=2))
sys.exit(0 if out["pass"] else 1)
