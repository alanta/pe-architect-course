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
# Step 2: Package vulnerability scan
# ---------------------------------------------------------------------------
echo "==> Building vuln-check image and scanning packages..."
docker build --target vuln-check -t emoji-api-vuln-check "${SCRIPT_DIR}"

VULN_JSON="$(docker run --rm emoji-api-vuln-check)"

VULN_PACKAGES="$(echo "${VULN_JSON}" | jq '[.projects[].frameworks[]? | (.topLevelPackages[]?, .transitivePackages[]?) | select(has("vulnerabilities"))]')"
CRITICAL_COUNT="$(echo "${VULN_PACKAGES}" | jq '[.[].vulnerabilities[] | select(.severity == "Critical")] | length')"
HIGH_COUNT="$(echo "${VULN_PACKAGES}"     | jq '[.[].vulnerabilities[] | select(.severity == "High")]     | length')"
MODERATE_COUNT="$(echo "${VULN_PACKAGES}" | jq '[.[].vulnerabilities[] | select(.severity == "Moderate" or .severity == "Medium")] | length')"
LOW_COUNT="$(echo "${VULN_PACKAGES}"      | jq '[.[].vulnerabilities[] | select(.severity == "Low")]      | length')"
VULN_COUNT=$(( CRITICAL_COUNT + HIGH_COUNT + MODERATE_COUNT + LOW_COUNT ))

if [[ "${CRITICAL_COUNT}" -gt 0 ]]; then
    echo "[ERROR] Found ${CRITICAL_COUNT} critical package vulnerability(ies) — build blocked."
    echo "${VULN_JSON}" | jq -r '
        .projects[] | select(.frameworks) |
        .path as $proj |
        .frameworks[] |
        ((.topLevelPackages // [])[], (.transitivePackages // [])[]) |
        select(has("vulnerabilities")) |
        "  \($proj): \(.id) \(.resolvedVersion) — \(.vulnerabilities[].severity) (\(.vulnerabilities[].advisoryurl))"
    '
    exit 1
fi

if [[ "${VULN_COUNT}" -gt 0 ]]; then
    echo "[WARN] Found package vulnerabilities (critical=${CRITICAL_COUNT} high=${HIGH_COUNT} moderate=${MODERATE_COUNT} low=${LOW_COUNT}) — non-critical, continuing."
fi

echo "==> No package vulnerabilities found."

# ---------------------------------------------------------------------------
# Step 3: Publish package vulnerability counts to the Gatekeeper ConfigMap
# ---------------------------------------------------------------------------
PKG_VULN_CONFIGMAP="${PKG_VULN_CONFIGMAP:-pkg-vuln-data}"

if kubectl get configmap "${PKG_VULN_CONFIGMAP}" -n "${COVERAGE_NAMESPACE}" &>/dev/null; then
    echo "==> Publishing package vuln counts to ConfigMap ${COVERAGE_NAMESPACE}/${PKG_VULN_CONFIGMAP}..."
    kubectl patch configmap "${PKG_VULN_CONFIGMAP}" \
        -n "${COVERAGE_NAMESPACE}" \
        --type merge \
        -p "{\"data\":{\"${COMMIT_SHA}.critical\":\"${CRITICAL_COUNT}\",\"${COMMIT_SHA}.high\":\"${HIGH_COUNT}\",\"${COMMIT_SHA}.moderate\":\"${MODERATE_COUNT}\",\"${COMMIT_SHA}.low\":\"${LOW_COUNT}\"}}"
    echo "==> Package vulnerability data published (commit ${COMMIT_SHA}: critical=${CRITICAL_COUNT} high=${HIGH_COUNT} moderate=${MODERATE_COUNT} low=${LOW_COUNT})."
else
    echo "[WARN] ConfigMap ${COVERAGE_NAMESPACE}/${PKG_VULN_CONFIGMAP} not found — skipping publish."
    echo "       Apply workshop/capoc/cve/apply.sh first."
fi

# ---------------------------------------------------------------------------
# Step 5: Publish coverage to the Gatekeeper ConfigMap
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
# Step 6: Build Docker image variants and load into kind
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

# ---------------------------------------------------------------------------
# Step 7: Build the red demo variant with a hardcoded low-coverage SHA
# ---------------------------------------------------------------------------
RED_DEMO_SHA="demo-low-cov"
RED_DEMO_COVERAGE="35"

echo "==> Building emoji-api:red (demo blocked variant)..."
docker build \
    --build-arg COLOR="red" \
    -t "emoji-api:red" \
    "${SCRIPT_DIR}"
echo "==> Loading emoji-api:red into kind cluster '${KIND_CLUSTER}'..."
kind load docker-image "emoji-api:red" --name "${KIND_CLUSTER}"

if kubectl get configmap "${COVERAGE_CONFIGMAP}" -n "${COVERAGE_NAMESPACE}" &>/dev/null; then
    echo "==> Registering demo low-coverage SHA ${RED_DEMO_SHA}=${RED_DEMO_COVERAGE}% in ConfigMap..."
    kubectl patch configmap "${COVERAGE_CONFIGMAP}" \
        -n "${COVERAGE_NAMESPACE}" \
        --type merge \
        -p "{\"data\":{\"${RED_DEMO_SHA}\":\"${RED_DEMO_COVERAGE}\"}}"
fi

if kubectl get configmap "${PKG_VULN_CONFIGMAP}" -n "${COVERAGE_NAMESPACE}" &>/dev/null; then
    echo "==> Registering demo package vuln SHA ${RED_DEMO_SHA} (critical=2 high=3) in ConfigMap..."
    kubectl patch configmap "${PKG_VULN_CONFIGMAP}" \
        -n "${COVERAGE_NAMESPACE}" \
        --type merge \
        -p "{\"data\":{\"${RED_DEMO_SHA}.critical\":\"2\",\"${RED_DEMO_SHA}.high\":\"3\",\"${RED_DEMO_SHA}.moderate\":\"1\",\"${RED_DEMO_SHA}.low\":\"4\"}}"
fi

echo ""
echo "Built images:"
for color in "${COLORS[@]}"; do
    echo "  emoji-api:${color}  (EMOJI_COLOR=${color})"
done
echo "  emoji-api:red  (EMOJI_COLOR=red) - low coverage demo variant"