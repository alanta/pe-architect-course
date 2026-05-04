#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
KIND_CLUSTER="${KIND_CLUSTER:-5min-idp}"
COLORS=(purple green orange)

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
