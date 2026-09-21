#!/usr/bin/env bash
# Prepare an isolated PostgreSQL client path for the upstream Puppeteer harness.
set -euo pipefail

root_dir="$(cd "$(dirname "$0")/../../.." && pwd)"
environment_dir="$root_dir/tools/grow-team/test-environment"
state_dir="$environment_dir/.state/puppeteer-postgres-client"
client_dir="$state_dir/bin"
container="grow-team-agent-test-database-1"

prepare_client() {
    mkdir -p "$client_dir"
    cat >"$client_dir/psql" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail

# This wrapper is only for the dedicated Grow Team test database. It removes
# host options because PostgreSQL runs in the named rootless test container.
container="grow-team-agent-test-database-1"
args=()
while (($#)); do
    case "$1" in
        -h|--host)
            shift 2
            ;;
        --host=*)
            shift
            ;;
        *)
            args+=("$1")
            shift
            ;;
    esac
done

case " ${args[*]} " in
    *" zulip_test "*|*" zulip_test_template "*|*" zulip_test_base "*|*" postgres "*) ;;
    *)
        echo "Scoped psql accepts only Grow Team test databases." >&2
        exit 64
        ;;
esac

exec docker exec -i -u postgres "$container" env -u PGUSER -u PGPASSWORD psql "${args[@]}"
EOF
    chmod 0755 "$client_dir/psql"
}

preflight() {
    prepare_client
    docker inspect --format '{{.State.Running}} {{.State.Health.Status}}' "$container" \
        | grep -qx 'true healthy'
    docker exec "$container" test -x /usr/local/bin/psql
    printf '%s\n' "Scoped Puppeteer PostgreSQL client is ready: $client_dir/psql"
}

ensure_language_name_map() {
    if [[ -s "$root_dir/locale/language_name_map.json" ]]; then
        return
    fi

    "$environment_dir/run.sh" "$root_dir/.venv/bin/python" -c '
import django
django.setup()
from zerver.management.commands.compilemessages import Command
command = Command()
command.extract_language_options()
command.create_language_name_map()
'
}

case "${1:---preflight}" in
    --preflight)
        preflight
        ;;
    --run)
        if [[ "${GROW_TEAM_BROWSER_DB_HANDOFF:-}" != "1" ]]; then
            echo "Database handoff is required before the browser harness can reset zulip_test." >&2
            exit 64
        fi
        preflight
        shift
        cd "$root_dir"
        export PATH="$client_dir:$PATH"
        export PUPPETEER_CACHE_DIR="$environment_dir/.state/puppeteer"
        export PUPPETEER_SKIP_CHROME_HEADLESS_SHELL_DOWNLOAD=true
        export PUPPETEER_SKIP_FIREFOX_DOWNLOAD=true
        ensure_language_name_map
        exec "$environment_dir/run.sh" "$root_dir/tools/test-js-with-puppeteer" "$@"
        ;;
    *)
        echo "Usage: $0 [--preflight|--run [puppeteer arguments...]]" >&2
        exit 64
        ;;
esac
