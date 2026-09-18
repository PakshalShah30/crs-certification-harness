#!/usr/bin/env python3
"""
End-to-end certification runner (Newman path).

What it does, in order:
  1. Starts the mock CRS API (uvicorn) as a subprocess.
  2. Waits for GET /health to return 200 (with a timeout).
  3. Resets in-memory mock state via POST /_test/reset, for a clean run.
  4. Runs the Postman collection with Newman, requesting cli+json+htmlextra
     reporters. Falls back to cli+json if htmlextra isn't installed.
  5. Stops the mock API.
  6. Generates reports/certification-report.md (+ .html) from the Newman
     JSON output via scripts/generate_signoff.py.

Exit code mirrors Newman's exit code (non-zero if any assertion failed).
This is expected and correct for this repo: the bundled mock has 3
deliberate defects, so a "healthy" run of this script against the bundled
mock exits non-zero. See README.md.

Usage:
    python3 scripts/run_certification.py [--partner "Some Partner Name"]
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import time
import urllib.request
import urllib.error
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HOST = "127.0.0.1"
PORT = 8000
BASE_URL = f"http://{HOST}:{PORT}"
HEALTH_TIMEOUT_SECONDS = 20


def log(msg: str) -> None:
    print(f"[run_certification] {msg}", flush=True)


def wait_for_health(timeout: float) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"{BASE_URL}/health", timeout=1) as resp:
                if resp.status == 200:
                    return True
        except (urllib.error.URLError, ConnectionError, OSError):
            pass
        time.sleep(0.3)
    return False


def reset_state() -> None:
    try:
        req = urllib.request.Request(f"{BASE_URL}/_test/reset", method="POST", data=b"")
        urllib.request.urlopen(req, timeout=2)
    except Exception as exc:  # noqa: BLE001
        log(f"warning: could not reset mock state ({exc})")


def find_newman() -> list[str] | None:
    """Return the command prefix to invoke newman, or None if unavailable."""
    npx = shutil.which("npx")
    newman_bin = shutil.which("newman")
    if newman_bin:
        return [newman_bin]
    if npx:
        # `npx newman` will use a locally-installed newman (node_modules/.bin)
        # if present, without hitting the network, as long as it's already
        # installed (this repo's README documents `npm install`).
        return [npx, "--no-install", "newman"]
    return None


def has_htmlextra() -> bool:
    return (ROOT / "node_modules" / "newman-reporter-htmlextra").exists()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--partner", default="Reference Mock CRS (bundled with this repo)")
    parser.add_argument("--skip-server", action="store_true", help="Assume the mock API is already running")
    args = parser.parse_args()

    reports_dir = ROOT / "reports"
    reports_dir.mkdir(exist_ok=True)

    server_proc = None
    if not args.skip_server:
        log(f"Starting mock CRS API on {BASE_URL} ...")
        server_proc = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "mock_crs.app:app", "--host", HOST, "--port", str(PORT)],
            cwd=str(ROOT),
            stdout=open(reports_dir / "mock_crs_server.log", "w"),
            stderr=subprocess.STDOUT,
        )

    exit_code = 1
    try:
        log("Waiting for mock CRS API health check ...")
        if not wait_for_health(HEALTH_TIMEOUT_SECONDS):
            log("ERROR: mock CRS API did not become healthy in time.")
            return 3
        log("Mock CRS API is healthy.")

        reset_state()

        newman_cmd = find_newman()
        if newman_cmd is None:
            log("ERROR: Newman/npx not found. Falling back is not automatic here -- "
                "run `python3 scripts/run_python_tests.py` instead, or `npm install` first.")
            return 4

        reporters = "cli,json,htmlextra" if has_htmlextra() else "cli,json"
        if reporters == "cli,json":
            log("newman-reporter-htmlextra not found under node_modules/ -- "
                "falling back to cli,json reporters. Run `npm install` for the HTML report.")

        cmd = newman_cmd + [
            "run",
            str(ROOT / "collection" / "crs-certification.postman_collection.json"),
            "-e",
            str(ROOT / "collection" / "local.postman_environment.json"),
            "-r",
            reporters,
            "--reporter-json-export",
            str(reports_dir / "newman-report.json"),
        ]
        if "htmlextra" in reporters:
            cmd += ["--reporter-htmlextra-export", str(reports_dir / "newman-report.html")]

        log(f"Running: {' '.join(cmd)}")
        result = subprocess.run(cmd, cwd=str(ROOT))
        exit_code = result.returncode

    finally:
        if server_proc is not None:
            log("Stopping mock CRS API ...")
            server_proc.terminate()
            try:
                server_proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                server_proc.kill()

    log("Generating certification sign-off report ...")
    signoff_cmd = [
        sys.executable,
        str(ROOT / "scripts" / "generate_signoff.py"),
        "--newman-json",
        str(reports_dir / "newman-report.json"),
        "--out",
        str(reports_dir / "certification-report.md"),
        "--html-out",
        str(reports_dir / "certification-report.html"),
        "--partner",
        args.partner,
    ]
    subprocess.run(signoff_cmd, cwd=str(ROOT), check=False)

    log(f"Done. Newman exit code: {exit_code}. See reports/certification-report.md")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
