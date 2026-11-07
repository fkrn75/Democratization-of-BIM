#!/bin/sh
# 컨테이너 안에서 실행: BimCraft zip 을 tmpfs 에 풀고 로컬 정적 서버로 띄운 뒤 Chromium 탐침을 돌린다.
set -e
python3 -m zipfile -e /in/bc.zip /tmp/app
cd /tmp/app/bimcraft-browser-edition-v0.8.1
python3 -m http.server 8000 --bind 127.0.0.1 > /tmp/http.log 2>&1 &
sleep 1
cd /out
node /probe/bimcraft_probe.mjs
