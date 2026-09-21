#!/usr/bin/env bash
# Run one Grow Team test command with this worktree's dependencies.
set -euo pipefail

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)
runtime_lib_dir="$repo_root/tools/grow-team/test-environment/.runtime-lib"
vips_runtime_lib_dir="$repo_root/tools/grow-team/test-environment/.runtime-vips-lib"

[[ -x "$repo_root/.venv/bin/python" ]] || {
    echo "Missing $repo_root/.venv. Run setup-dependencies.sh first." >&2
    exit 1
}
[[ -f "$runtime_lib_dir/libldap-2.5.so.0" ]] || {
    echo "Missing OpenLDAP runtime. Run setup-dependencies.sh first." >&2
    exit 1
}
[[ -f "$vips_runtime_lib_dir/libvips.so.42" ]] || {
    echo "Missing libvips runtime. Run setup-dependencies.sh first." >&2
    exit 1
}

export LD_LIBRARY_PATH="$vips_runtime_lib_dir:$runtime_lib_dir${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
export DISABLE_MANDATORY_SECRET_CHECK=True
export DJANGO_SETTINGS_MODULE=zproject.test_settings
export ZULIP_DB_NAME=zulip_test
source "$repo_root/.venv/bin/activate"
exec "$@"
