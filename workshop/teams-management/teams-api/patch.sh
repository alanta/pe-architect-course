#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
KIND_CLUSTER="${KIND_CLUSTER:-5min-idp}"
IMAGE="teams-api:local"
DEPLOYMENT="teams-api"
NAMESPACE="teams-api"

echo "==> Building ${IMAGE}..."
docker build -t "${IMAGE}" "${SCRIPT_DIR}"

echo "==> Loading ${IMAGE} into kind cluster '${KIND_CLUSTER}'..."
kind load docker-image "${IMAGE}" --name "${KIND_CLUSTER}"

echo "==> Restarting rollout ${DEPLOYMENT} in ${NAMESPACE}..."
kubectl rollout restart deployment/"${DEPLOYMENT}" -n "${NAMESPACE}"
kubectl rollout status deployment/"${DEPLOYMENT}" -n "${NAMESPACE}" --timeout=60s

echo "==> Done. API is live."
