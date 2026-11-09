"""결정성 검사: 별도 프로세스로 RA-01 을 두 번 다시 만들어 커밋된 RA-01.ifc / 골든과 바이트 단위로 비교한다."""
import hashlib, subprocess, sys, tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ref_ifc = hashlib.sha256((HERE / "RA-01.ifc").read_bytes()).hexdigest()
ref_gold = hashlib.sha256((HERE / "RA-01.golden.json").read_bytes()).hexdigest()
ok = True
for i in (1, 2):
    with tempfile.TemporaryDirectory() as d:
        # 골든도 같이 재생성하려면 기본 경로를 쓰는 대신 임시 위치로 실행한다
        code = ("import build_ra01, pathlib, sys;"
                f"build_ra01.run(pathlib.Path(r'{d}/RA-01.ifc'), pathlib.Path(r'{d}/g.json'))")
        subprocess.run([sys.executable, "-I", "-c", f"import sys; sys.path.insert(0, r'{HERE}'); {code}"], check=True)
        a = hashlib.sha256((Path(d) / "RA-01.ifc").read_bytes()).hexdigest()
        b = hashlib.sha256((Path(d) / "g.json").read_bytes()).hexdigest()
        print(f"실행 {i}: ifc {'일치' if a == ref_ifc else '불일치'}, golden {'일치' if b == ref_gold else '불일치'}")
        ok &= a == ref_ifc and b == ref_gold
print("결정성:", "통과" if ok else "실패")
sys.exit(0 if ok else 1)
