#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel)"
VENV="${REPO_ROOT}/venv"

"${VENV}/bin/python" "${REPO_ROOT}/scripts/validate-hacs.py"
