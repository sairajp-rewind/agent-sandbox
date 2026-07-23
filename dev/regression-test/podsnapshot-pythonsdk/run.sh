#!/usr/bin/env bash
# Copyright 2026 The Kubernetes Authors.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.


set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"

# Preserve original exit code so test failures properly propagate to CI
trap 'rc=$?; "${SCRIPT_DIR}/post-test.sh" || true; exit $rc' EXIT

echo "=== 1. Checking Python Environment ==="
python3 --version | grep -E "Python 3\.(1[0-9]|[2-9][0-9])" || {
    echo "ERROR: Python 3.10+ is required"
    exit 1
}

echo "=== 2. Running Pre-test Setup ==="
"${SCRIPT_DIR}/pre-test.sh"

echo "=== 3. Creating & Activating Virtual Environment ==="
python3 -m venv "${SCRIPT_DIR}/.venv"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/.venv/bin/activate"

echo "=== 4. Installing SDK & Test Dependencies ==="
pip install --upgrade pip
pip install -r "${SCRIPT_DIR}/requirements.txt"
pip install -e "${REPO_ROOT}/clients/python/agentic-sandbox-client/[test]"

echo "=== 5. Running Pytest Regression Suite ==="
pytest -v --junitxml="${SCRIPT_DIR}/results.xml" "${SCRIPT_DIR}"

echo "=== All Tests Completed Successfully ==="
