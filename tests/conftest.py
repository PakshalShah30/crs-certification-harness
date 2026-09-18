"""
Shared pytest fixtures for the pure-Python certification test path.

This starts the real mock CRS API (mock_crs.app:app) as a uvicorn subprocess
for the whole test session, waits for it to become healthy, resets its
state before each test module, and tears it down at the end. Tests then
talk to it over plain HTTP with `requests`, exactly like Newman does --
this proves the SAME business rules the Postman collection encodes, just
without a Node/Newman dependency.
"""
from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

import pytest
import requests

ROOT = Path(__file__).resolve().parent.parent
BASE_URL = "http://127.0.0.1:8001"  # different port than the Newman path, so both can run independently
HEALTH_TIMEOUT_SECONDS = 20


@pytest.fixture(scope="session")
def base_url():
    return BASE_URL


@pytest.fixture(scope="session", autouse=True)
def mock_crs_server():
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "mock_crs.app:app", "--host", "127.0.0.1", "--port", "8001"],
        cwd=str(ROOT),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.time() + HEALTH_TIMEOUT_SECONDS
        healthy = False
        while time.time() < deadline:
            try:
                resp = requests.get(f"{BASE_URL}/health", timeout=1)
                if resp.status_code == 200:
                    healthy = True
                    break
            except requests.exceptions.RequestException:
                pass
            time.sleep(0.2)
        if not healthy:
            proc.terminate()
            raise RuntimeError("mock CRS API did not become healthy in time")
        yield
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


@pytest.fixture(autouse=True)
def reset_mock_state(base_url):
    """Ensure every test starts from a clean reservation store."""
    requests.post(f"{base_url}/_test/reset", timeout=5)
    yield
