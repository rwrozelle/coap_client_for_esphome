#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel)"
VENV="${REPO_ROOT}/.venv-validation"

"${VENV}/bin/python" "${REPO_ROOT}/scripts/validate-hacs.py"
