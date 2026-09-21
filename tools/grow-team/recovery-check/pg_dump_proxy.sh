#!/usr/bin/env bash
# Provide the pinned PostgreSQL client only inside the rehearsal mount namespace.
set -euo pipefail

: "${GROW_TEAM_RECOVERY_DOCKER_HOST:?Missing verified local Docker endpoint.}"
: "${GROW_TEAM_RECOVERY_NETWORK:?Missing private recovery network.}"
: "${GROW_TEAM_RECOVERY_IMAGE:?Missing pinned PostgreSQL image.}"
: "${PGPASSWORD:?pg_dump did not supply a database password.}"

output_path=""
args=()
for argument in "$@"; do
    case "$argument" in
        --file=*) output_path=${argument#--file=} ;;
        --host=*|--port=*) ;;
        *) args+=("$argument") ;;
    esac
done

[[ -n "$output_path" && "$output_path" = /* ]] || {
    echo "Recovery pg_dump requires an absolute output directory." >&2
    exit 2
}
output_parent=$(dirname "$output_path")
output_name=$(basename "$output_path")
[[ "$output_name" != .* && "$output_name" != */* && -d "$output_parent" ]] || {
    echo "Recovery pg_dump received an unsafe output directory." >&2
    exit 2
}

docker --host "$GROW_TEAM_RECOVERY_DOCKER_HOST" run --rm \
    --network "$GROW_TEAM_RECOVERY_NETWORK" \
    --mount "type=bind,src=$output_parent,dst=/recovery-output" \
    --env PGPASSWORD \
    --entrypoint /usr/local/bin/pg_dump \
    "$GROW_TEAM_RECOVERY_IMAGE" \
    "${args[@]}" \
    --host=source \
    "--file=/recovery-output/$output_name"
