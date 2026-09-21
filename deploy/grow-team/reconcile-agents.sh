#!/usr/bin/env bash
set -euo pipefail

deploy_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)

# Keep the lock and deadline inside the container if the Docker client exits.
exec "$deploy_dir/compose.sh" exec -T --interactive=false --user zulip zulip \
  sh -c 'umask 077; exec /usr/bin/timeout --signal=TERM --kill-after=5s 60s \
    /usr/bin/flock --no-fork --nonblock --conflict-exit-code 75 \
    /data/grow-team-agent-private/reconcile.lock \
    /home/zulip/deployments/current/.venv/bin/python -B \
    /home/zulip/deployments/current/manage.py reconcile_agents --limit 100'
