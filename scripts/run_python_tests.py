#!/usr/bin/env python3
"""
Pure-Python fallback certification runner.

Use this when Node.js / Newman is not available in the environment. It runs
the same certification logic as the Postman collection (see
tests/test_certification.py), starting and stopping the mock CRS API itself
via pytest fixtures (tests/conftest.py) -- no manual server management
needed.

Usage:
    python3 scripts/run_python_tests.py

Exit code is pytest's exit code:
  0 = all tests passed (including the 3 deliberate quirks correctly
      xfail-ing, which pytest treats as success)
  1 = at least one test failed unexpectedly, OR a quirk test unexpectedly
      PASSED (XPASS with strict=True), meaning a documented defect no
      longer reproduces and this suite / the README need updating.
"""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    cmd = [sys.executable, "-m", "pytest", "-v", "tests/"]
    print(f"[run_python_tests] Running: {' '.join(cmd)}")
    result = subprocess.run(cmd, cwd=str(ROOT))
    return result.returncode


if __name__ == "__main__":
    sys.exit(main())
