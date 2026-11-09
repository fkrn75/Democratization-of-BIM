#!/usr/bin/env bash
# 2단계 시험 이미지 재현 빌드. 다운로드 파일은 해시 검증 후 빌드 컨텍스트 폴더(DL)에 둔다.
#   DL/ccad/cc.tar.gz  = constructcad-v0.9.1-x86_64-unknown-linux-gnu.tar.gz  (sha256 755dbebf…2b6f)
#   DL/bimcraft/bc.zip = bimcraft-browser-edition-v0.8.1.zip                   (sha256 93ccdf25…673a)
# 베이스 이미지 digest 는 각 Dockerfile 에 고정돼 있다. 시험 실행은 모두 --network none (run_ccad.sh, run_node.sh 참고).
set -e
cd "$(dirname "$0")/.."
docker build -t s0-ccad -f phase2/docker/Dockerfile.ccad "$DL/ccad"
docker build -t s0-ccad-cjk -f phase2/docker/Dockerfile.ccad-cjk phase2/docker
docker build -t s0-pylinux -f phase2/docker/Dockerfile.pylinux phase2/docker
docker build -t s0-iflite -f phase2/docker/Dockerfile.iflite phase2/docker
docker build -t s0-bimcraft -f phase2/docker/Dockerfile.bimcraft phase2/docker
docker build -t s0-pascal -f phase2/docker/Dockerfile.pascal phase2/docker
