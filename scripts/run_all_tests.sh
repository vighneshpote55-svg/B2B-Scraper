#!/usr/bin/env bash
# ==============================================================================
# LeadMap Pro — Unified Automated Test Runner
# Executes all test suites (Phases 1–4) in an isolated, deterministic environment.
# ==============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

cd "${REPO_ROOT}"

echo "================================================================="
echo "🧪 Running Full LeadMap Pro Automated Regression & Release Suite"
echo "================================================================="

export PYTHONPATH="${REPO_ROOT}:${PYTHONPATH:-}"
export LEADMAP_ENV="test"

TEST_DATA_DIR="$(mktemp -d /tmp/leadmap_test_data_XXXXXX)"
export DATA_DIR="${TEST_DATA_DIR}"

SERVER_STARTED=0
SERVER_PID=""

cleanup() {
    if [ "${SERVER_STARTED}" -eq 1 ] && [ -n "${SERVER_PID}" ]; then
        echo "[*] Stopping background test dashboard server (PID ${SERVER_PID})..."
        kill -TERM "${SERVER_PID}" 2>/dev/null || true
        wait "${SERVER_PID}" 2>/dev/null || true
    fi
    if [ -d "${TEST_DATA_DIR}" ]; then
        rm -rf "${TEST_DATA_DIR}"
    fi
}
trap cleanup EXIT INT TERM

# Check if dashboard server is already running on port 3000
if ! curl -s -f http://127.0.0.1:3000/api/status >/dev/null 2>&1; then
    echo "[*] Starting isolated background dashboard server on 127.0.0.1:3000 for integration tests..."
    python3 "${REPO_ROOT}/dashboard.py" >/dev/null 2>&1 &
    SERVER_PID=$!
    SERVER_STARTED=1

    # Wait for server ready probe
    READY=0
    for i in {1..30}; do
        if curl -s -f http://127.0.0.1:3000/api/status >/dev/null 2>&1; then
            READY=1
            echo "✔ Dashboard test server is online."
            break
        fi
        sleep 0.2
    done

    if [ "${READY}" -ne 1 ]; then
        echo "❌ Failed to start dashboard server on port 3000."
        exit 1
    fi
else
    echo "[*] Using existing dashboard server on 127.0.0.1:3000."
fi

TEST_FILES=(
    "tests/test_phase4_e2e_journey.py"
    "tests/test_phase4_browser_ux.py"
    "tests/test_phase4_security_hardening.py"
    "tests/test_phase4_reliability_resilience.py"
    "tests/test_phase4_observability.py"
    "tests/test_phase3_quality_and_ux.py"
    "tests/test_phase2_reliability.py"
    "tests/test_phase1b_security.py"
    "tests/test_ssrf_and_config_phase5.py"
    "tests/test_security_phase1.py"
    "tests/test_security_phase2.py"
)

TOTAL_FAILED=0
TOTAL_PASSED=0

for test_file in "${TEST_FILES[@]}"; do
    echo ""
    echo "[RUNNING] ${test_file}..."
    if python3 -m unittest "${test_file}"; then
        echo "✔ [PASSED] ${test_file}"
        TOTAL_PASSED=$((TOTAL_PASSED + 1))
    else
        echo "❌ [FAILED] ${test_file}"
        TOTAL_FAILED=$((TOTAL_FAILED + 1))
    fi
done

echo ""
echo "================================================================="
echo "🏁 Test Suite Execution Summary:"
echo "    Total Test Suites: $((TOTAL_PASSED + TOTAL_FAILED))"
echo "    Passed:            ${TOTAL_PASSED}"
echo "    Failed:            ${TOTAL_FAILED}"
echo "================================================================="

if [ "${TOTAL_FAILED}" -eq 0 ]; then
    echo "🎉 ALL TESTS PASSED! LeadMap Pro is ready for production release."
    exit 0
else
    echo "❌ SOME TESTS FAILED. Release blocked."
    exit 1
fi
