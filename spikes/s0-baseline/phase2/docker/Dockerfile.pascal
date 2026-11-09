# Pascal CLI 격리 시험 이미지: npm 설치 단계에서만 네트워크, --ignore-scripts, 버전 고정, lockfile 기록. npx 미사용.
FROM node@sha256:5ec23b7c622b00547753bc6ffd78085dd868a68aeb41c8e0a1418f033e13d96d
RUN useradd -m -u 10001 tester && mkdir -p /opt/pascal /out && chown -R tester /opt/pascal /out
USER 10001
WORKDIR /opt/pascal
RUN npm init -y >/dev/null && npm install --ignore-scripts --save-exact --no-audit --no-fund @pascal-app/cli@1.0.6 2>&1 | tail -3 && cp package-lock.json lock.copy.json
ENV PATH=/opt/pascal/node_modules/.bin:$PATH HOME=/home/tester
WORKDIR /out
