#!/usr/bin/env bash
set -euo pipefail

deploy_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
unset DOCKER_CONTEXT
export DOCKER_HOST=unix:///run/grow-team-docker/docker.sock
export DOCKER_CONFIG=/opt/grow-team/lib/docker
exec /opt/grow-team/bin/docker --host="$DOCKER_HOST" compose \
  --project-name grow-team \
  --project-directory "$deploy_dir" \
  --file "$deploy_dir/compose.yaml" \
  --file "$deploy_dir/compose.override.yaml" \
  "$@"
