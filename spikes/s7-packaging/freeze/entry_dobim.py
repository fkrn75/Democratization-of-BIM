# SPDX-License-Identifier: MPL-2.0
"""동결본 CLI 진입점 (dobim.exe). PyInstaller 는 -m 실행이 없어 별도 진입점이 필요하다."""
import multiprocessing
import sys

if __name__ == "__main__":
    multiprocessing.freeze_support()   # 동결본에서 자식 프로세스가 재귀 실행되지 않게 한다
    from dobim_core.cli import main
    sys.exit(main())
