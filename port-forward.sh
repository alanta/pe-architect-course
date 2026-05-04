#!/usr/bin/env bash
# Development port-forward script
# Forwards the nginx ingress controller to port 80 so all *.localhost
# hostnames route through it, plus individual forwards for monitoring tools.
#
# Service              Namespace              Local → Cluster
# Nginx Ingress        ingress-nginx          80    → 80
#   teams-ui.localhost, teams-api.localhost, platform-auth.localhost
# Grafana              monitoring             3000  → 80
# Prometheus           monitoring             9090  → 9090
# AlertManager         monitoring             9093  → 9093

set -euo pipefail

PIDS=()

cleanup() {
  echo ""
  echo "Stopping port-forwards..."
  for pid in "${PIDS[@]}"; do
    kill "$pid" 2>/dev/null || true
  done
  echo "Done."
}
trap cleanup EXIT INT TERM

start_forward() {
  local label="$1"
  local namespace="$2"
  local resource="$3"
  local ports="$4"

  if ! kubectl get "$resource" -n "$namespace" &>/dev/null; then
    echo "[SKIP] $label ($resource not found in namespace '$namespace')"
    return
  fi

  kubectl port-forward -n "$namespace" "$resource" "$ports" &>/dev/null &
  local pid=$!
  PIDS+=("$pid")
  echo "[OK]   $label  →  http://localhost:${ports%%:*}  (pid $pid)"
}

echo "Starting development port-forwards..."
echo ""

start_forward "Nginx Ingress"  ingress-nginx  svc/ingress-nginx-controller  8080:80
start_forward "Grafana"        monitoring      svc/grafana-stack             3000:80
start_forward "Prometheus"     monitoring      svc/prometheus-operated       9090:9090
start_forward "AlertManager"   monitoring      svc/alertmanager-operated     9093:9093

echo ""
echo "All port-forwards running. Press Ctrl+C to stop."
echo ""
  echo "  Teams UI     http://teams-ui.localhost:8080"
  echo "  Teams API    http://teams-api.localhost:8080"
  echo "  Keycloak     http://platform-auth.localhost:8080"
echo "  Grafana      http://localhost:3000   (admin / admin123)"
echo "  Prometheus   http://localhost:9090"
echo "  AlertManager http://localhost:9093"
echo ""

# Wait until interrupted
wait
