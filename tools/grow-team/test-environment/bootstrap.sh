#!/usr/bin/env bash
# Prepare test-only PostgreSQL and test fixtures. Do not use this script for production data.
set -euo pipefail

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)
environment_dir="$repo_root/tools/grow-team/test-environment"
runtime_lib_dir="$environment_dir/.runtime-lib"
vips_runtime_lib_dir="$environment_dir/.runtime-vips-lib"
compose=(docker compose --project-directory "$environment_dir" --file "$environment_dir/compose.yaml")
secrets_file="$environment_dir/.env"
dev_secrets_file="$repo_root/zproject/dev-secrets.conf"

require() {
    command -v "$1" >/dev/null 2>&1 || {
        echo "Missing required command: $1" >&2
        exit 1
    }
}

require docker
require openssl
require ss

existing_container=$("${compose[@]}" ps --quiet database)
if ss -ltnH 'sport = :5432' | grep -q .; then
    if [[ -z "$existing_container" ]] || ! docker port "$existing_container" 5432/tcp | grep -qx '127.0.0.1:5432'; then
        echo "Port 127.0.0.1:5432 is in use by another service. Do not reuse that service." >&2
        exit 1
    fi
fi

if [[ ! -f "$secrets_file" ]]; then
    umask 077
    postgres_password=$(openssl rand -hex 32)
    test_db_password=$(openssl rand -hex 32)
    {
        printf 'POSTGRES_PASSWORD=%s\n' "$postgres_password"
        printf 'TEST_DB_PASSWORD=%s\n' "$test_db_password"
    } >"$secrets_file"
fi

# The upstream test settings read this ignored development-only file. It contains
# only the generated password for this dedicated local database.
if [[ ! -f "$dev_secrets_file" ]]; then
    test_db_password=$(sed -n 's/^TEST_DB_PASSWORD=//p' "$secrets_file")
    umask 077
    {
        printf '[secrets]\n'
        printf 'local_database_password=%s\n' "$test_db_password"
    } >"$dev_secrets_file"
fi
if ! grep -q '^secret_key[[:space:]]*=' "$dev_secrets_file"; then
    umask 077
    printf 'secret_key=%s\n' "$(openssl rand -hex 32)" >>"$dev_secrets_file"
fi
if ! grep -q '^zulip_org_id[[:space:]]*=' "$dev_secrets_file"; then
    umask 077
    printf 'zulip_org_id=%s\n' "$(cat /proc/sys/kernel/random/uuid)" >>"$dev_secrets_file"
fi

"${compose[@]}" up --detach --wait

# Keep PostgreSQL administration in the dedicated container. The host does not
# need psql, pg_dump, a PostgreSQL service, or an entry in ~/.pgpass.
"${compose[@]}" exec --no-TTY database sh -eu -c '
psql -v ON_ERROR_STOP=1 -U postgres -d postgres --set=test_password="$TEST_DB_PASSWORD" <<'"'"'SQL'"'"'
SELECT format('"'"'CREATE ROLE zulip_test LOGIN PASSWORD %L CREATEDB'"'"', :'"'"'test_password'"'"')
WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '"'"'zulip_test'"'"')
\gexec
ALTER ROLE zulip_test PASSWORD :'"'"'test_password'"'"';
SELECT '"'"'CREATE DATABASE zulip_test_base OWNER zulip_test'"'"'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = '"'"'zulip_test_base'"'"')
\gexec
SELECT '"'"'CREATE DATABASE zulip_test OWNER zulip_test TEMPLATE zulip_test_base'"'"'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = '"'"'zulip_test'"'"')
\gexec
SQL
psql -v ON_ERROR_STOP=1 -U postgres -d zulip_test_base -c '"'"'CREATE EXTENSION IF NOT EXISTS pgroonga'"'"'
psql -v ON_ERROR_STOP=1 -U postgres -d zulip_test -c '"'"'CREATE EXTENSION IF NOT EXISTS pgroonga'"'"'
'

if [[ ! -x "$repo_root/.venv/bin/python" ]]; then
    echo "Missing $repo_root/.venv. Run setup-dependencies.sh first." >&2
    exit 1
fi
if [[ ! -f "$runtime_lib_dir/libldap-2.5.so.0" ]]; then
    echo "Missing OpenLDAP runtime. Run setup-dependencies.sh first." >&2
    exit 1
fi
if [[ ! -f "$vips_runtime_lib_dir/libvips.so.42" ]]; then
    echo "Missing libvips runtime. Run setup-dependencies.sh first." >&2
    exit 1
fi
if [[ ! -d "$repo_root/node_modules" ]]; then
    echo "Missing $repo_root/node_modules. Run setup-dependencies.sh first." >&2
    exit 1
fi

# Generate only missing ignored development secrets for the upstream test settings.
# The command writes no secret values to stdout.
(
    cd "$repo_root"
    export LD_LIBRARY_PATH="$vips_runtime_lib_dir:$runtime_lib_dir${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
    .venv/bin/python scripts/setup/generate_secrets.py --development
)

if [[ ! -f "$repo_root/var/.grow-team-agent-test-template-ready" ]]; then
    (
        cd "$repo_root"
        mkdir -p var/uploads
        export LD_LIBRARY_PATH="$vips_runtime_lib_dir:$runtime_lib_dir${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
        export DISABLE_MANDATORY_SECRET_CHECK=True
        export DJANGO_SETTINGS_MODULE=zproject.test_settings
        export ZULIP_DB_NAME=zulip_test
        tools/grow-team/test-environment/prepare-generated-assets.sh
        .venv/bin/python manage.py migrate --noinput
        .venv/bin/python manage.py populate_db --test-suite -n30 --threads=1 \
            --max-topics=3 --direct-message-groups=0 --personals=0 \
            --percent-direct-message-groups=0 --percent-personals=0
        "${compose[@]}" exec --no-TTY database sh -eu -c '
psql -v ON_ERROR_STOP=1 -U postgres -d postgres <<'"'"'SQL'"'"'
SELECT '"'"'CREATE DATABASE zulip_test_template OWNER zulip_test TEMPLATE zulip_test'"'"'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = '"'"'zulip_test_template'"'"')
\gexec
SQL
'
        .venv/bin/python - <<'PY'
import os

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "zproject.test_settings")
import django

django.setup()
from zerver.lib.test_fixtures import TEST_DATABASE

TEST_DATABASE.write_new_migration_digest()
TEST_DATABASE.write_new_db_digest()
PY
        tools/webpack --test
        touch var/.grow-team-agent-test-template-ready
    )
fi

echo "Test PostgreSQL and fixtures are ready."
