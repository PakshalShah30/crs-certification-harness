#!/usr/bin/env bash
# Thin, portable wrapper around scripts/run_certification.py.
#
# Usage:
#   ./run_certification.sh                       # default partner name
#   ./run_certification.sh --partner "Acme Corp"  # forwarded to the Python runner
#
# Exit code mirrors Newman's: non-zero if any assertion failed. Against the
# bundled mock CRS this is EXPECTED (3 deliberate quirks are designed to
# fail) -- see README.md.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

PYTHON_BIN="${PYTHON_BIN:-python3}"

exec "$PYTHON_BIN" scripts/run_certification.py "$@"
