#!/usr/bin/env bash
# Sets up the pre-commit validation environment.
# Run once after cloning, or to rebuild after deleting .venv-validation.
set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel)"
UV="${HOME}/.local/bin/uv"

echo "==> Installing uv (if needed)..."
if ! command -v uv &>/dev/null && [ ! -x "${UV}" ]; then
    curl -LsSf https://astral.sh/uv/install.sh | sh
fi
UV="$(command -v uv 2>/dev/null || echo "${UV}")"

echo "==> Installing Python 3.14 (if needed)..."
"${UV}" python install 3.14

echo "==> Creating .venv-validation..."
"${UV}" venv --python 3.14 "${REPO_ROOT}/.venv-validation"

echo "==> Installing validation packages..."
"${UV}" pip install \
    --python "${REPO_ROOT}/.venv-validation/bin/python" \
    homeassistant tqdm ruff awesomeversion voluptuous

echo "==> Installing pre-commit (if needed)..."
PRECOMMIT_VENV="${HOME}/.local/venvs/pre-commit"
if [ ! -x "${PRECOMMIT_VENV}/bin/pre-commit" ]; then
    python3 -m venv "${PRECOMMIT_VENV}"
    "${PRECOMMIT_VENV}/bin/pip" install pre-commit --quiet
fi

echo "==> Installing git hooks..."
"${PRECOMMIT_VENV}/bin/pre-commit" install

echo ""
echo "Done. Git commit will now run hassfest and HACS validation automatically."
echo "Requires ~/dev/core (the HA core repo) to be present for hassfest."
