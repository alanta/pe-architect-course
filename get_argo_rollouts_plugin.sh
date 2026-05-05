#!/usr/bin/env bash
set -euo pipefail

BINARY="kubectl-argo-rollouts-linux-amd64"
INSTALL_PATH="/usr/local/bin/kubectl-argo-rollouts"

echo "Downloading Argo Rollouts kubectl plugin..."
curl -LO "https://github.com/argoproj/argo-rollouts/releases/latest/download/${BINARY}"

chmod +x "./${BINARY}"
sudo mv "./${BINARY}" "${INSTALL_PATH}"

echo "Installed: $(kubectl argo rollouts version)"
