#!/usr/bin/env bash
# 지정한 ccad 명령의 입력 스키마를 한 줄 요약으로 출력
for c in "$@"; do ./phase2/run_ccad.sh out/p2/ccad/o1 ccad schema $c | .venv/Scripts/python.exe -X utf8 -c "
import json,sys;d=json.load(sys.stdin);s=d['input_schema'];print('##',d['name'],d['kind'],'|',d['description'][:230].replace(chr(10),' '));print('  req:',s.get('required'),'props:',{k:(v.get('type') or v.get('enum') or '?') for k,v in s.get('properties',{}).items()})"
done
