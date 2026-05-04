#!/usr/bin/env bash

# Runs on every workspace start. Idempotently ensures bash completion and aliases
# are present in .bashrc so they survive workspace restarts.

if ! grep -q "bash_completion" "$HOME/.bashrc"; then
  echo "source /usr/share/bash-completion/bash_completion" >> "$HOME/.bashrc"
fi

if ! grep -q "kubectl completion bash" "$HOME/.bashrc"; then
  echo "source <(kubectl completion bash)" >> "$HOME/.bashrc"
  echo "complete -F __start_kubectl k" >> "$HOME/.bashrc"
fi

if ! grep -q "alias k='kubectl'" "$HOME/.bashrc"; then
  echo "alias k='kubectl'" >> "$HOME/.bashrc"
  echo "alias kg='kubectl get'" >> "$HOME/.bashrc"
  echo "alias h='humctl'" >> "$HOME/.bashrc"
  echo "alias sk='score-k8s'" >> "$HOME/.bashrc"
fi

# Add *.localhost entries so Python's DNS resolver can reach cluster services.
# curl resolves these fine but Python's socket module does not handle *.localhost subdomains.
LOCALHOST_HOSTS=(
  "teams-api.localhost"
  "teams-ui.localhost"
  "platform-auth.localhost"
  "rollouts-demo.localhost"
)
for host in "${LOCALHOST_HOSTS[@]}"; do
  if ! grep -q "$host" /etc/hosts; then
    echo "127.0.0.1 $host" | sudo tee -a /etc/hosts > /dev/null
  fi
done
