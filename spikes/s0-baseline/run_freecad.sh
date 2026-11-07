#!/usr/bin/env bash
# FreeCADCmd 를 격리된 설정/사용자 폴더로 실행한다 (사용자 %APPDATA% 를 건드리지 않음).
# 사용: FC=<FreeCAD 압축 해제 폴더> PROFILE=<빈 폴더> ./run_freecad.sh script.py [FreeCADCmd 추가 인자]
set -e
export FREECAD_USER_HOME="$(cygpath -w "$PROFILE/user")"
mkdir -p "$PROFILE/user"
exec "$FC/bin/FreeCADCmd.exe" -u "$(cygpath -w "$PROFILE/user.cfg")" -s "$(cygpath -w "$PROFILE/sys.cfg")" "$@"
