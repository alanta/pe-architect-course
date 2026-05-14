#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
KIND_CLUSTER="${KIND_CLUSTER:-5min-idp}"
COLORS=(purple green orange)
COVERAGE_CONFIGMAP="${COVERAGE_CONFIGMAP:-code-coverage-data}"
COVERAGE_NAMESPACE="${COVERAGE_NAMESPACE:-gatekeeper-system}"

# ---------------------------------------------------------------------------
# Step 1: Run tests and collect coverage (inside SDK container — no local tooling needed)
# ---------------------------------------------------------------------------
echo "==> Building test image and running tests inside container..."
docker build --target test -t emoji-api-test "${SCRIPT_DIR}"

COVERAGE_PCT="$(docker run --rm emoji-api-test)"
COMMIT_SHA="$(git -C "${SCRIPT_DIR}" rev-parse --short HEAD 2>/dev/null || echo "unknown")"

echo "==> Coverage: ${COVERAGE_PCT}% for commit ${COMMIT_SHA}"

# ---------------------------------------------------------------------------
# Step 2: Publish coverage to the Gatekeeper ConfigMap
# ---------------------------------------------------------------------------
if kubectl get configmap "${COVERAGE_CONFIGMAP}" -n "${COVERAGE_NAMESPACE}" &>/dev/null; then
    echo "==> Publishing coverage to ConfigMap ${COVERAGE_NAMESPACE}/${COVERAGE_CONFIGMAP}..."
    kubectl patch configmap "${COVERAGE_CONFIGMAP}" \
        -n "${COVERAGE_NAMESPACE}" \
        --type merge \
        -p "{\"data\":{\"${COMMIT_SHA}\":\"${COVERAGE_PCT}\"}}"
    echo "==> Coverage data published."
else
    echo "[WARN] ConfigMap ${COVERAGE_NAMESPACE}/${COVERAGE_CONFIGMAP} not found — skipping publish."
    echo "       Apply workshop/capoc/quality/coverage-configmap.yaml first."
fi

# ---------------------------------------------------------------------------
# Step 3: Build Docker image variants and load into kind
# ---------------------------------------------------------------------------
build_variant() {
    local color="$1"
    local tag="emoji-api:${color}"
    echo "==> Building ${tag}..."
    docker build \
        --build-arg COLOR="${color}" \
        -t "${tag}" \
        "${SCRIPT_DIR}"
    echo "==> Loading ${tag} into kind cluster '${KIND_CLUSTER}'..."
    kind load docker-image "${tag}" --name "${KIND_CLUSTER}"
    echo "==> Done: ${tag}"
}

# If a specific color is passed as argument, build only that one
if [[ $# -gt 0 ]]; then
    build_variant "$1"
else
    for color in "${COLORS[@]}"; do
        build_variant "${color}"
    done
fi

echo ""
echo "Built images:"
for color in "${COLORS[@]}"; do
    echo "  emoji-api:${color}  (EMOJI_COLOR=${color})"
done
