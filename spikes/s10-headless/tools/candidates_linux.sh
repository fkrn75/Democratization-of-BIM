#!/bin/sh
# 컨테이너 안에서 실행: 렌더 후보(wgpu+lavapipe, VTK-OSMesa)를 시험한다. 네트워크 필요(apt·pip). 코어 이미지와 별개의 일회용 컨테이너.
# 사용: docker run --rm -v <s10>:/s10:ro -v <s0>:/s0:ro python:3.12-slim sh /s10/tools/candidates_linux.sh
set -u
export DEBIAN_FRONTEND=noninteractive PYTHONDONTWRITEBYTECODE=1 PIP_DISABLE_PIP_VERSION_CHECK=1
echo "## wgpu + lavapipe"
apt-get update -qq >/dev/null 2>&1
apt-get install -y -qq --no-install-recommends mesa-vulkan-drivers libvulkan1 >/dev/null 2>&1
echo "apt GUI/GL 계열 패키지(드라이버가 끌고 온 것):"
dpkg -l | awk '{print $2}' | grep -Ei 'libx11|libxcb|libdrm|wayland|libgl|mesa|libxext|libxrender|libxshm|libxfixes|libxrandr|libxxf86' | tr '\n' ' '; echo
pip install -q wgpu numpy ifcopenshell==0.9.0.post1 >/dev/null 2>&1
mkdir -p /tmp/o
python /s10/tools/wgpu_probe.py /s0/RA-01.ifc /tmp/o/wgpu.png --fallback 2>&1 | tail -3
echo "## VTK OSMesa"
pip install -q --extra-index-url https://wheels.vtk.org vtk-osmesa >/dev/null 2>&1 && python /s10/tools/vtk_probe.py /s0/RA-01.ifc /tmp/o/vtk.png 2>&1 | tail -3 || echo "vtk-osmesa 설치 실패"
pip list 2>/dev/null | grep -Ei '^(vtk|wgpu|rendercanvas)'
pip show vtk-osmesa 2>/dev/null | grep -Ei '^(version|license)'
[ -d /out ] && cp /tmp/o/*.png /out/ 2>/dev/null; true
