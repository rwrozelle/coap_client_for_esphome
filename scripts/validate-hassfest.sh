#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel)"
VENV="${REPO_ROOT}/.venv-validation"
HA_CORE="${HOME}/dev/core"

echo "Running hassfest validation..."
cd "${HA_CORE}"
PATH="${VENV}/bin:${PATH}" \
PYTHONPATH="${HA_CORE}" \
"${VENV}/bin/python" -m script.hassfest \
  --action validate \
  --integration-path "${REPO_ROOT}/custom_components/coap_client_for_esphome"
