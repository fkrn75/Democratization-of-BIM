#!/usr/bin/env bash
# 노드 계열 도구 격리 실행: RA-01 읽기 전용, 출력 폴더 하나만 쓰기, 네트워크 없음.
# 사용: IMG=s0-iflite phase2/run_node.sh <출력폴더> <명령...>
set -e
OUT="$(cd "$1" 2>/dev/null && pwd || (mkdir -p "$1" && cd "$1" && pwd))"; shift
exec docker run --rm -i --network none --cap-drop=ALL --security-opt no-new-privileges --memory 2g --cpus 2 --pids-limit 256 \
  --read-only --tmpfs /tmp:size=256m -v "$(cygpath -w "$PWD/RA-01.ifc"):/in/RA-01.ifc:ro" -v "$(cygpath -w "$OUT"):/out" ${IMG:-s0-iflite} "$@"
