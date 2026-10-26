#!/usr/bin/env bash
# Writes the SSH client config for the "server" alias from GitHub secrets (passed as env vars).
set -euo pipefail
install -d -m 700 ~/.ssh
printf '%s\n' "$SSH_PRIVATE_KEY" | tr -d '\r' > ~/.ssh/deploy_key
chmod 600 ~/.ssh/deploy_key
if [ -n "${SSH_KEY_PASSPHRASE:-}" ]; then
  # Decrypt the runner's temporary copy only; the key on the server is unchanged.
  ssh-keygen -p -q -P "$SSH_KEY_PASSPHRASE" -N "" -f ~/.ssh/deploy_key >/dev/null \
    || { echo "::error title=Key passphrase::SSH_KEY_PASSPHRASE does not unlock SSH_PRIVATE_KEY"; exit 1; }
fi
port="${SSH_PORT:-22}"
if [ -n "${SSH_KNOWN_HOSTS:-}" ]; then
  printf '%s\n' "$SSH_KNOWN_HOSTS" > ~/.ssh/known_hosts
else
  echo "::warning title=Host key not pinned::Add SSH_KNOWN_HOSTS so the server identity is verified"
  ssh-keyscan -p "$port" -T 15 "$SSH_HOST" > ~/.ssh/known_hosts 2>/dev/null
fi
chmod 600 ~/.ssh/known_hosts
cat > ~/.ssh/config <<CFG
Host server
  HostName $SSH_HOST
  User $SSH_USER
  Port $port
  IdentityFile ~/.ssh/deploy_key
  IdentitiesOnly yes
  StrictHostKeyChecking yes
  BatchMode yes
  ConnectTimeout 20
  ServerAliveInterval 15
CFG
chmod 600 ~/.ssh/config
