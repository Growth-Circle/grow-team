#!/usr/bin/env bash
# Rehearse one isolated agent backup and restore. Do not use production data.
set -euo pipefail

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)
check_dir="$repo_root/tools/grow-team/recovery-check"
runs_dir="$check_dir/.runs"
image='zulip/zulip-postgresql:14@sha256:e71ba8616fa42cdc1b248f51263d9290c29681cb8c1992eb9b498af0bb656b29'
runtime_lib_dir="$repo_root/tools/grow-team/test-environment/.runtime-lib"
vips_runtime_lib_dir="$repo_root/tools/grow-team/test-environment/.runtime-vips-lib"
expected_docker_host="unix:///run/user/$(id -u)/docker.sock"

require() {
    command -v "$1" >/dev/null 2>&1 || {
        echo "Missing required command: $1" >&2
        exit 1
    }
}

verify_local_rootless_docker() {
    if [[ -n ${DOCKER_HOST:-} && $DOCKER_HOST != "$expected_docker_host" ]]; then
        echo "Recovery requires the local rootless Docker endpoint." >&2
        exit 1
    fi
    if [[ -n ${DOCKER_CONTEXT:-} && $DOCKER_CONTEXT != rootless ]]; then
        echo "Recovery requires the rootless Docker context." >&2
        exit 1
    fi
    [[ $(docker context show) == rootless ]] || {
        echo "Recovery requires the rootless Docker context." >&2
        exit 1
    }
    local context_host
    context_host=$(docker context inspect rootless --format '{{.Endpoints.docker.Host}}')
    [[ $context_host == "$expected_docker_host" && -S "/run/user/$(id -u)/docker.sock" ]] || {
        echo "Recovery requires the current user's local Docker socket." >&2
        exit 1
    }
    docker --host "$expected_docker_host" info --format '{{json .SecurityOptions}}' | grep -q 'name=rootless' || {
        echo "Recovery requires a rootless Docker engine." >&2
        exit 1
    }
}

assert_absent() {
    local kind=$1
    local name=$2
    if docker --host "$expected_docker_host" "$kind" inspect "$name" >/dev/null 2>&1; then
        echo "Recovery resource collision: $kind $name already exists." >&2
        exit 1
    fi
}

target_is_empty() {
    local target_container=$1
    local target_database=$2
    local object_count
    # shellcheck disable=SC2016 # Expand TEST_DB_PASSWORD only inside the dedicated container.
    object_count=$(docker --host "$expected_docker_host" exec "$target_container" sh -eu -c '
PGPASSWORD="$TEST_DB_PASSWORD" psql -h localhost -U zulip_test -d "$1" -Atc "
SELECT count(*)
FROM pg_class AS relation
JOIN pg_namespace AS namespace ON namespace.oid = relation.relnamespace
WHERE namespace.nspname NOT IN ('\''pg_catalog'\'', '\''information_schema'\'')
  AND relation.relkind IN ('\''r'\'', '\''p'\'', '\''v'\'', '\''m'\'', '\''S'\'', '\''f'\'');
"' sh "$target_database")
    [[ $object_count == 0 ]]
}

assert_target_empty() {
    target_is_empty "$@" || {
        echo "Recovery target is already used. Refuse to restore over it." >&2
        return 1
    }
}

if [[ ${1:-} == --probe-resource-collision ]]; then
    [[ $# == 2 ]] || { echo "Usage: $0 --probe-resource-collision NETWORK" >&2; exit 2; }
    verify_local_rootless_docker
    if docker --host "$expected_docker_host" network inspect "$2" >/dev/null 2>&1; then
        echo "Foreign recovery network is refused without mutation." >&2
        exit 1
    fi
    echo "The supplied network does not exist." >&2
    exit 2
fi

if [[ ${1:-} == --probe-used-target ]]; then
    [[ $# == 2 ]] || { echo "Usage: $0 --probe-used-target STATE_DIR" >&2; exit 2; }
    verify_local_rootless_docker
    state_path=$(realpath "$2")
    [[ $state_path == "$check_dir/.runs/"* && -f "$state_path/ownership.json" && -f "$state_path/containers.json" ]] || {
        echo "The target probe requires an owned recovery state directory." >&2
        exit 2
    }
    # The owned state file contains generated local test credentials.
    # shellcheck source=/dev/null
    source "$state_path/compose.env"
    target_container=$("$repo_root/.venv/bin/python" -c 'import json, sys; print(json.load(open(sys.argv[1]))["target"])' "$state_path/containers.json")
    invocation=$("$repo_root/.venv/bin/python" -c 'import json, sys; print(json.load(open(sys.argv[1]))["invocation"])' "$state_path/ownership.json")
    target_database=$("$repo_root/.venv/bin/python" -c 'import json, sys; state=json.load(open(sys.argv[1])); print(state.get("target_database", "grow_team_agent_recovery_target_" + state["invocation"]))' "$state_path/ownership.json")
    [[ $(docker --host "$expected_docker_host" inspect --format '{{index .Config.Labels "com.grow-team.recovery.invocation"}}' "$target_container") == "$invocation" ]] || {
        echo "The target probe refuses a foreign container." >&2
        exit 1
    }
    docker --host "$expected_docker_host" container start "$target_container" >/dev/null
    trap 'docker --host "$expected_docker_host" container stop "$target_container" >/dev/null 2>&1 || true' EXIT
    for _ in {1..20}; do
        if docker --host "$expected_docker_host" exec "$target_container" pg_isready -U postgres -d postgres >/dev/null; then
            break
        fi
        sleep 1
    done
    if assert_target_empty "$target_container" "$target_database"; then
        echo "The target probe expected a used database." >&2
        exit 2
    fi
    echo "Used target is refused without restore mutation." >&2
    exit 1
fi

require bwrap
require docker
require openssl
require ss
[[ -x "$repo_root/.venv/bin/python" ]] || { echo "Missing the scoped Python environment." >&2; exit 1; }
[[ -f "$runtime_lib_dir/libldap-2.5.so.0" && -f "$vips_runtime_lib_dir/libvips.so.42" ]] || {
    echo "Missing the scoped native runtime libraries." >&2
    exit 1
}
verify_local_rootless_docker

umask 077
mkdir -p "$runs_dir"
chmod 700 "$runs_dir"
invocation_id="$(date -u +%Y%m%d%H%M%S)-$(openssl rand -hex 6)"
state_dir="$runs_dir/$invocation_id"
mkdir -m 700 "$state_dir" || { echo "Recovery state collision." >&2; exit 1; }
project="grow-team-agent-recovery-$invocation_id"
network="$project-network"
source_volume="$project-source-data"
target_volume="$project-target-data"
source_database="grow_team_agent_recovery_source_$invocation_id"
target_database="grow_team_agent_recovery_target_$invocation_id"
source_port=$((56000 + 16#${invocation_id: -2} % 1000))
target_port=$((source_port + 1))
while ss -ltnH "sport = :$source_port or sport = :$target_port" | grep -q .; do
    source_port=$((source_port + 2))
    target_port=$((source_port + 1))
done

assert_absent network "$network"
assert_absent volume "$source_volume"
assert_absent volume "$target_volume"

{
    printf 'POSTGRES_PASSWORD=%s\n' "$(openssl rand -hex 32)"
    printf 'TEST_DB_PASSWORD=%s\n' "$(openssl rand -hex 32)"
} >"$state_dir/compose.env"
chmod 600 "$state_dir/compose.env"
# shellcheck disable=SC1091
source "$state_dir/compose.env"
export POSTGRES_PASSWORD TEST_DB_PASSWORD
export GROW_TEAM_RECOVERY_ID="$invocation_id"
export GROW_TEAM_RECOVERY_PROJECT="$project"
export GROW_TEAM_RECOVERY_NETWORK="$network"
export GROW_TEAM_RECOVERY_SOURCE_VOLUME="$source_volume"
export GROW_TEAM_RECOVERY_TARGET_VOLUME="$target_volume"
export GROW_TEAM_RECOVERY_SOURCE_PORT="$source_port"
export GROW_TEAM_RECOVERY_TARGET_PORT="$target_port"
compose=(docker --host "$expected_docker_host" compose --project-directory "$check_dir" --project-name "$project" --env-file "$state_dir/compose.env" --file "$check_dir/compose.yaml")

source_revision=$(git -C "$repo_root" rev-parse HEAD)
relevant_file_hashes=$(sha256sum \
    "$repo_root/zerver/management/commands/backup.py" \
    "$repo_root/zerver/lib/agent_backup.py" \
    "$repo_root/zerver/models/agents.py" | awk '{print $1 " " $2}')
export GROW_TEAM_RECOVERY_STATE="$state_dir"
export GROW_TEAM_RECOVERY_DB_PASSWORD="$TEST_DB_PASSWORD"
export GROW_TEAM_RECOVERY_IMAGE="$image"
export GROW_TEAM_RECOVERY_DOCKER_HOST="$expected_docker_host"
export GROW_TEAM_RECOVERY_SOURCE_REVISION="$source_revision"
export GROW_TEAM_RECOVERY_REVIEWED_BACKUP_COMMIT=bafbc67a27640e81e056ace9c9b5ce1eaaeb27a1
export GROW_TEAM_RECOVERY_RELEVANT_FILE_HASHES="$relevant_file_hashes"
export LD_LIBRARY_PATH="$vips_runtime_lib_dir:$runtime_lib_dir${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
export DISABLE_MANDATORY_SECRET_CHECK=True
export DJANGO_SETTINGS_MODULE=recovery_settings
export PYTHONPATH="$repo_root:$check_dir${PYTHONPATH:+:$PYTHONPATH}"

printf '{"invocation":"%s","docker_host":"%s","project":"%s","target_database":"%s"}\n' \
    "$invocation_id" "$expected_docker_host" "$project" "$target_database" >"$state_dir/ownership.json"
chmod 600 "$state_dir/ownership.json"

source_container=''
target_container=''
stop_owned_containers() {
    [[ -z $source_container ]] || docker --host "$expected_docker_host" container stop "$source_container" >/dev/null 2>&1 || true
    [[ -z $target_container ]] || docker --host "$expected_docker_host" container stop "$target_container" >/dev/null 2>&1 || true
}

"${compose[@]}" up --detach --wait
source_container=$("${compose[@]}" ps --quiet source)
target_container=$("${compose[@]}" ps --quiet target)
[[ -n $source_container && -n $target_container ]] || { echo "Recovery containers were not created." >&2; exit 1; }
for container in "$source_container" "$target_container"; do
    [[ $(docker --host "$expected_docker_host" inspect --format '{{index .Config.Labels "com.grow-team.recovery.invocation"}}' "$container") == "$invocation_id" ]] || {
        echo "Recovery container ownership is invalid." >&2
        exit 1
    }
done
printf '{"source":"%s","target":"%s"}\n' "$source_container" "$target_container" >"$state_dir/containers.json"
chmod 600 "$state_dir/containers.json"
trap stop_owned_containers EXIT

# shellcheck disable=SC2016 # Expand TEST_DB_PASSWORD and $1 only inside the dedicated container.
docker --host "$expected_docker_host" exec "$source_container" sh -eu -c '
psql -v ON_ERROR_STOP=1 -U postgres -d postgres --set=test_password="$TEST_DB_PASSWORD" <<'"'"'SQL'"'"'
SELECT format('"'"'CREATE ROLE zulip_test LOGIN PASSWORD %L CREATEDB'"'"', :'"'"'test_password'"'"')
\gexec
SQL
createdb -U postgres -O zulip_test "$1"
psql -v ON_ERROR_STOP=1 -U postgres -d "$1" -c "CREATE EXTENSION pgroonga"
' sh "$source_database"
# shellcheck disable=SC2016 # Expand TEST_DB_PASSWORD and $1 only inside the dedicated container.
docker --host "$expected_docker_host" exec "$target_container" sh -eu -c '
psql -v ON_ERROR_STOP=1 -U postgres -d postgres --set=test_password="$TEST_DB_PASSWORD" <<'"'"'SQL'"'"'
SELECT format('"'"'CREATE ROLE zulip_test LOGIN PASSWORD %L CREATEDB'"'"', :'"'"'test_password'"'"')
\gexec
SQL
createdb -U postgres -O zulip_test "$1"
psql -v ON_ERROR_STOP=1 -U postgres -d "$1" -c "ALTER ROLE zulip_test IN DATABASE \"$1\" SET search_path TO zulip, public;"
' sh "$target_database"
assert_target_empty "$target_container" "$target_database"

export GROW_TEAM_RECOVERY_DB_PORT="$source_port"
export GROW_TEAM_RECOVERY_DB_NAME="$source_database"
"$repo_root/.venv/bin/python" "$check_dir/rehearsal.py" guard-probes
"$repo_root/.venv/bin/python" "$check_dir/rehearsal.py" prepare-source

bwrap --die-with-parent --unshare-user --uid "$(id -u)" --gid "$(id -g)" \
    --tmpfs / --dir /usr --dir /usr/lib --dir /usr/lib/postgresql \
    --dir /usr/lib/postgresql/14 --dir /usr/lib/postgresql/14/bin \
    --ro-bind /usr/bin /usr/bin --ro-bind /usr/local /usr/local \
    --ro-bind /usr/lib/python3.12 /usr/lib/python3.12 \
    --ro-bind /usr/lib/x86_64-linux-gnu /usr/lib/x86_64-linux-gnu \
    --ro-bind /usr/lib/locale /usr/lib/locale --ro-bind /usr/lib/os-release /usr/lib/os-release \
    --ro-bind /lib /lib --ro-bind /lib64 /lib64 --ro-bind /bin /bin --ro-bind /etc /etc \
    --ro-bind /home /home --bind /tmp /tmp --bind "$repo_root" "$repo_root" \
    --bind "/run/user/$(id -u)" "/run/user/$(id -u)" \
    --ro-bind "$check_dir/pg_dump_proxy.sh" /usr/lib/postgresql/14/bin/pg_dump \
    --proc /proc --dev /dev -- "$repo_root/.venv/bin/python" "$check_dir/rehearsal.py" backup

extract_dir="$state_dir/extracted"
mkdir -m 700 "$extract_dir"
export GROW_TEAM_RECOVERY_ARCHIVE="$state_dir/backup.tar.gz"
export GROW_TEAM_RECOVERY_EXTRACT="$extract_dir"
"$repo_root/.venv/bin/python" - <<'PY'
import os
import tarfile
from pathlib import Path

previous_umask = os.umask(0o077)
try:
    with tarfile.open(Path(os.environ["GROW_TEAM_RECOVERY_ARCHIVE"])) as archive:
        archive.extractall(Path(os.environ["GROW_TEAM_RECOVERY_EXTRACT"]), filter="data")
finally:
    os.umask(previous_umask)
PY
assert_target_empty "$target_container" "$target_database"

docker --host "$expected_docker_host" run --rm --network "$network" \
    --mount "type=bind,src=$extract_dir/zulip-backup/database,dst=/dump,readonly" \
    --env PGPASSWORD="$POSTGRES_PASSWORD" --entrypoint /usr/local/bin/pg_restore "$image" \
    --host=target --username=postgres --dbname="$target_database" --exit-on-error /dump

private_target="$state_dir/target-private"
mkdir -m 700 "$private_target"
cp -a "$extract_dir/zulip-backup/agent/artifacts" "$private_target/artifacts"
cp -a "$extract_dir/zulip-backup/agent/keyring.json" "$private_target/keyring.json"
chmod 700 "$private_target/artifacts"
chmod 600 "$private_target/keyring.json"

export GROW_TEAM_RECOVERY_DB_PORT="$target_port"
export GROW_TEAM_RECOVERY_DB_NAME="$target_database"
"$repo_root/.venv/bin/python" "$check_dir/rehearsal.py" verify-target

"$repo_root/.venv/bin/python" - <<'PY'
import json
import os
from datetime import datetime, timezone
from pathlib import Path

state = Path(os.environ["GROW_TEAM_RECOVERY_STATE"])
evidence = json.loads((state / "evidence.json").read_text())
evidence["completed_at"] = datetime.now(timezone.utc).isoformat()
evidence["engine"] = {"docker_host": os.environ["GROW_TEAM_RECOVERY_DOCKER_HOST"], "rootless": True}
(state / "completed.json").write_text(json.dumps(evidence, sort_keys=True, indent=2) + "\n")
os.chmod(state / "completed.json", 0o600)
PY

stop_owned_containers
trap - EXIT
echo "Isolated recovery rehearsal completed. Private evidence: $state_dir/completed.json"
