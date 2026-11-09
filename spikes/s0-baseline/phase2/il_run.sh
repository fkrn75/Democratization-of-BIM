#!/usr/bin/env bash
# ifc-lite SDK 스크립트 실행: 스크립트는 /out/scripts, 결과는 stdout. IFC 본문은 <<<IFC ... IFC>>> 마커 사이를 호스트가 추출한다.
# 사용: il_run.sh script.js outfile.ifc
set -e
cd "$(dirname "$0")/.."
RAW="out/p2/iflite/$2.raw"
docker run --rm -i --network none --cap-drop=ALL --security-opt no-new-privileges --memory 2g --cpus 2 --read-only --tmpfs /tmp:size=256m \
  -v "$(cygpath -w $PWD/RA-01.ifc):/in/RA-01.ifc:ro" -v "$(cygpath -w $PWD/out/p2/iflite):/out" s0-iflite ifc-lite run "/out/scripts/$1" /in/RA-01.ifc > "$RAW" 2>&1 || true
sed -n '/^<<<IFC$/,/^IFC>>>$/p' "$RAW" | sed '1d;$d' > "out/p2/iflite/$2"
grep -v -E "^<<<IFC|^IFC>>>" "$RAW" | grep -v -E "^(ISO-10303|HEADER|ENDSEC|DATA|FILE_|END-ISO|#)" | head -20
