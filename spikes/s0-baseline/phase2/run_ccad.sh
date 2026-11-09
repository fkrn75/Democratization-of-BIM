#!/usr/bin/env bash
# ConstructCAD 격리 실행: 입력 읽기 전용(/in), 출력 전용 폴더 하나만 쓰기(/out), 네트워크 없음, 모든 capability 제거.
# 사용: run_ccad.sh <출력폴더(호스트)> ccad <인자...>   (stdin 은 그대로 전달)
set -e
OUT="$(cd "$1" 2>/dev/null && pwd || (mkdir -p "$1" && cd "$1" && pwd))"; shift
exec docker run --rm -i --network none --cap-drop=ALL --security-opt no-new-privileges --memory 2g --cpus 2 --pids-limit 256 \
  --read-only --tmpfs /tmp:size=256m -v "$(cygpath -w "$PWD/RA-01.ifc"):/in/RA-01.ifc:ro" \
  -v "$(cygpath -w "$PWD/out/p2/in"):/in/extra:ro" -v "$(cygpath -w "$OUT"):/out" ${CCAD_IMG:-s0-ccad} "$@"
