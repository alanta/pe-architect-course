#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TEAMS_CLI="/workspaces/pe-architect-course/workshop/teams-management/cli/teams_cli.py"
OPERATOR_POLL_INTERVAL="${OPERATOR_POLL_INTERVAL:-30}"  # seconds between operator reconciles
NAMESPACE_WAIT_TIMEOUT="${NAMESPACE_WAIT_TIMEOUT:-90}"  # max seconds to wait for namespace
VALID_COLORS=(purple green orange)

usage() {
    echo "Usage: $0 --team <team-name> --color <purple|green|orange> [--good]"
    echo ""
    echo "  --team   Name of the team (must already exist via the Teams API)"
    echo "  --color  Emoji color variant to deploy"
    echo "  --good   Use deployment.good.yaml instead of deployment.yaml"
    echo ""
    echo "Example: $0 --team 'Platform Engineering' --color purple"
    echo "Example: $0 --team 'Platform Engineering' --color purple --good"
    exit 1
}

log_info()    { echo "[INFO]  $*"; }
log_success() { echo "[OK]    $*"; }
log_error()   { echo "[ERROR] $*" >&2; exit 1; }

wait_for_namespace() {
    local ns="$1"
    local deadline=$(( $(date +%s) + NAMESPACE_WAIT_TIMEOUT ))
    log_info "Waiting up to ${NAMESPACE_WAIT_TIMEOUT}s for operator to create namespace '$ns'..."
    while [[ $(date +%s) -lt $deadline ]]; do
        if kubectl get namespace "$ns" &>/dev/null; then
            echo ""
            log_success "Namespace '$ns' is ready"
            return 0
        fi
        echo -n "."
        sleep 5
    done
    echo ""
    log_error "Namespace '$ns' was not created within ${NAMESPACE_WAIT_TIMEOUT}s. Check operator logs."
}

# Parse args
TEAM_NAME=""
COLOR=""
USE_GOOD="false"
while [[ $# -gt 0 ]]; do
    case "$1" in
        --team)  TEAM_NAME="$2"; shift 2 ;;
        --color) COLOR="$2";     shift 2 ;;
        --good)  USE_GOOD="true"; shift ;;
        -h|--help) usage ;;
        *) log_error "Unknown argument: $1" ;;
    esac
done

[[ -z "$TEAM_NAME" ]] && { echo "Error: --team is required"; usage; }
[[ -z "$COLOR" ]]     && { echo "Error: --color is required"; usage; }

# Validate color
COLOR="${COLOR,,}"  # lowercase
valid=false
for c in "${VALID_COLORS[@]}"; do [[ "$c" == "$COLOR" ]] && valid=true; done
[[ "$valid" == false ]] && log_error "Invalid color '$COLOR'. Must be one of: ${VALID_COLORS[*]}"

# Sanitize team name -> namespace (mirrors operator logic)
sanitize_namespace() {
    local name="${1,,}"                             # lowercase
    name="${name//[^a-z0-9]/-}"                    # replace non-alnum with -
    name=$(echo "$name" | tr -s '-' | sed 's/^-//;s/-$//')  # collapse & strip leading/trailing -
    name="${name:0:58}"                             # max 63 - len("team-") = 58
    name="${name%-}"                               # strip trailing - after truncation
    echo "team-${name}"
}

export NAMESPACE
NAMESPACE="$(sanitize_namespace "$TEAM_NAME")"
export COLOR
export IMAGE="emoji-api:${COLOR}"
export TEAM_NAME
export COMMIT_SHA
COMMIT_SHA="$(git -C "$SCRIPT_DIR" rev-parse --short HEAD 2>/dev/null || echo "unknown")"

log_info "Team:      $TEAM_NAME"
log_info "Namespace: $NAMESPACE"
log_info "Color:     $COLOR"
log_info "Image:     $IMAGE"
log_info "Commit:    $COMMIT_SHA"

# Resolve the teams-api URL using curl (handles *.localhost DNS on Linux where Python may fail)
TEAMS_API_BASE="http://teams-api.localhost:8080"
if ! curl -sf "${TEAMS_API_BASE}/health" &>/dev/null; then
    log_error "Teams API not reachable at ${TEAMS_API_BASE}. Is the port-forward running?"
fi
export TEAMS_API_URL="$TEAMS_API_BASE"

# Ensure the team exists — create it via the CLI if not, then wait for the operator
if kubectl get namespace "$NAMESPACE" &>/dev/null; then
    log_info "Namespace '$NAMESPACE' already exists, skipping team creation"
else
    log_info "Creating team '$TEAM_NAME' via Teams CLI..."
    "$TEAMS_CLI" create "$TEAM_NAME"
    wait_for_namespace "$NAMESPACE"
fi

# Apply resources
DEPLOYMENT_FILE="deployment.yaml"
[[ "$USE_GOOD" == "true" ]] && DEPLOYMENT_FILE="deployment.good.yaml"
log_info "Deploying to namespace '$NAMESPACE' (using $DEPLOYMENT_FILE)..."
envsubst < "${SCRIPT_DIR}/k8s/${DEPLOYMENT_FILE}" | kubectl apply -f -

# Wait for rollout
log_info "Waiting for rollout..."
if [[ "$USE_GOOD" == "true" ]]; then
    kubectl argo rollouts status emoji-api -n "$NAMESPACE" --timeout 120s
else
    kubectl rollout status deployment/emoji-api -n "$NAMESPACE" --timeout=120s
fi

log_success "emoji-api:${COLOR} deployed to $NAMESPACE"
log_success "Reachable at: http://${NAMESPACE}.localhost:8080"
log_success "Scalar UI:    http://${NAMESPACE}.localhost:8080/scalar/v1"
