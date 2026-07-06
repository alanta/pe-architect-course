#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "==> Applying package vulnerability ConfigMap..."
kubectl apply -f "${SCRIPT_DIR}/pkg-vuln-configmap.yaml"

echo "==> Applying CVE ConstraintTemplate..."
kubectl apply -f "${SCRIPT_DIR}/cve-constraint-template.yaml"

echo "==> Waiting for ConstraintTemplate CRD to be established..."
for i in $(seq 1 30); do
    kubectl get crd vulnerabilityscan.constraints.gatekeeper.sh &>/dev/null && break
    echo "    ...waiting (${i}/30)"
    sleep 2
done
kubectl get crd vulnerabilityscan.constraints.gatekeeper.sh &>/dev/null || {
    echo "[ERROR] ConstraintTemplate CRD was not created within 60s"
    exit 1
}

echo "==> Applying CVE Constraint..."
kubectl apply -f "${SCRIPT_DIR}/cve-constraint.yaml"

echo ""
echo "Done. CVE constraint is active."
echo "  ConfigMap:          gatekeeper-system/pkg-vuln-data"
echo "  ConstraintTemplate: vulnerabilityscan"
echo "  Constraint:         enforce-vulnerability-scanning"
