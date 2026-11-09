"""외래 표본 내려받기 (저장소에 커밋하지 않는다).

출처: https://github.com/buildingSMART/Sample-Test-Files  (CC-BY-4.0, (C) buildingSMART International Ltd.)
파일: IFC 4.0.2.1 (IFC 4 ADD2 TC1)/Simple-Scene/Building-Architecture.ifc
사용: python fetch_samples.py [대상폴더]   (기본 out/samples, gitignore 대상)
SHA-256 이 기록값과 다르면 실패한다 (출처 변경 감지).
"""
import hashlib
import sys
import urllib.parse
import urllib.request
from pathlib import Path

REPO = "buildingSMART/Sample-Test-Files"
PATH = "IFC 4.0.2.1 (IFC 4 ADD2 TC1)/Simple-Scene/Building-Architecture.ifc"
SHA256 = "8790a1e193e82b8e7e7f337ec2633cd40f2120590317a1443503a25b079e2e80"  # 2026-11-07 main 기준
URL = f"https://raw.githubusercontent.com/{REPO}/main/" + urllib.parse.quote(PATH)

dest = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent / "out" / "samples"
dest.mkdir(parents=True, exist_ok=True)
out = dest / "Building-Architecture.ifc"
data = urllib.request.urlopen(URL, timeout=60).read()
h = hashlib.sha256(data).hexdigest()
if h != SHA256:
    sys.exit(f"SHA-256 불일치: {h} (기대 {SHA256}). 출처가 바뀌었을 수 있다. 저장하지 않음.")
out.write_bytes(data)
print(out, len(data), h)
