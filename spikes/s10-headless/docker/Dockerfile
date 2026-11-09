# S10: 깨끗한 Ubuntu 계열(Debian slim) 환경. 공식 python:3.12-slim 에 코어 의존성만 pip 로 설치한다.
# 빌드 컨텍스트 = tools/make_docker_ctx.py 가 만든 폴더(s1-core, s10, s0).
FROM python:3.12-slim
COPY s1-core /opt/dobim/s1-core
# 코어 + validate 추가 요소(pytest). GUI·렌더 패키지는 설치하지 않는다.
RUN pip install --no-cache-dir --disable-pip-version-check "/opt/dobim/s1-core[validate]" \
 && useradd -m app
COPY s10 /opt/dobim/s10
COPY s0 /opt/dobim/s0
USER app
WORKDIR /work
ENV PYTHONDONTWRITEBYTECODE=1
