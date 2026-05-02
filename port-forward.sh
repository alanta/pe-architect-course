#!/usr/bin/env bash
# Development port-forward script
# Starts all port-forwards in the background and cleans them up on exit.
#
# Service              Namespace              Local → Cluster
# Grafana              monitoring             3000  → 80
# Teams API            teams-api              3002  → 4200
# Prometheus           monitoring             9090  → 9090
# AlertManager         monitoring             9093  → 9093
# Keycloak             keycloak               8080  → 8080  (if namespace exists)

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

start_forward "Grafana"      monitoring  svc/grafana-stack            3000:80
start_forward "Teams API"    teams-api   svc/teams-api-service        3002:4200
start_forward "Prometheus"   monitoring  svc/prometheus-operated      9090:9090
start_forward "AlertManager" monitoring  svc/alertmanager-operated    9093:9093
start_forward "Keycloak"     keycloak    svc/keycloak-service         8080:8080

echo ""
echo "All port-forwards running. Press Ctrl+C to stop."
echo ""
echo "  Grafana      http://localhost:3000   (admin / admin123)"
echo "  Teams API    http://localhost:3002"
echo "  Prometheus   http://localhost:9090"
echo "  AlertManager http://localhost:9093"
echo "  Keycloak     http://localhost:8080"
echo ""

# Wait until interrupted
wait
