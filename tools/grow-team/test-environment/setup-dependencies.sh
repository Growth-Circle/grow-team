#!/usr/bin/env bash
# Install dependencies into this worktree only.
set -euo pipefail

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)
base_venv=${GROW_TEAM_BASE_VENV:-/home/ramaaditya/Project/.worktrees/grow-team-branding/.venv}
environment_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
wheelhouse="$environment_dir/.wheelhouse"
runtime_lib_dir="$environment_dir/.runtime-lib"
vips_runtime_lib_dir="$environment_dir/.runtime-vips-lib"
vips_host_lib_dir="$environment_dir/.runtime-vips-host-libs"
cd "$repo_root"

command -v uv >/dev/null 2>&1 || { echo "Missing required command: uv" >&2; exit 1; }
command -v pnpm >/dev/null 2>&1 || { echo "Missing required command: pnpm" >&2; exit 1; }
[[ -d "$base_venv/lib/python3.11/site-packages" ]] || {
    echo "Missing reusable Python dependency source: $base_venv" >&2
    exit 1
}

if [[ ! -x .venv/bin/python ]]; then
    uv venv --python python3.11 .venv
fi
# The lock intentionally builds psycopg2 from source. This host has no global
# PostgreSQL development headers. Copy the already provisioned local baseline
# into a new venv, then add the equivalent binary package only to this venv.
cp -a "$base_venv/lib/python3.11/site-packages/." .venv/lib/python3.11/site-packages/

# Build only the missing LDAP extension in an ephemeral rootless container.
# No development package is installed on the host.
mkdir -p "$wheelhouse"
if [[ ! -f "$wheelhouse/python_ldap-3.4.5-cp311-cp311-linux_x86_64.whl" ]]; then
    docker run --rm --name grow-team-agent-test-python-build \
        --entrypoint bash \
        --mount "type=bind,source=$wheelhouse,target=/wheelhouse" \
        python:3.11-slim-bookworm@sha256:4b4c524dc3dce996864e030c7bd9c6b0e517597189fee48f48e05b499442444b \
        -euc 'apt-get update && apt-get install --no-install-recommends --yes build-essential libldap2-dev libsasl2-dev && python -m pip wheel --wheel-dir /wheelhouse python-ldap==3.4.5'
fi
# The Python LDAP wheel needs OpenLDAP 2.5 at runtime. Keep those shared
# libraries beside this test environment because the host only has OpenLDAP 2.4.
mkdir -p "$runtime_lib_dir"
if [[ ! -f "$runtime_lib_dir/libldap-2.5.so.0" ]]; then
    docker run --rm --name grow-team-agent-test-ldap-runtime \
        --entrypoint bash \
        --mount "type=bind,source=$runtime_lib_dir,target=/runtime" \
        python:3.11-slim-bookworm@sha256:4b4c524dc3dce996864e030c7bd9c6b0e517597189fee48f48e05b499442444b \
        -euc 'apt-get update && apt-get install --no-install-recommends --yes libldap-2.5-0 && cp --remove-destination -L /usr/lib/x86_64-linux-gnu/libldap-2.5.so.0 /usr/lib/x86_64-linux-gnu/liblber-2.5.so.0 /runtime/'
fi

# Django imports pyvips during startup. Keep libvips and its dynamic dependency
# closure in this test environment because the host has no libvips runtime.
mkdir -p "$vips_runtime_lib_dir"
if [[ ! -f "$vips_runtime_lib_dir/libvips.so.42" ]]; then
    docker run --rm --name grow-team-agent-test-vips-runtime \
        --entrypoint bash \
        --mount "type=bind,source=$vips_runtime_lib_dir,target=/runtime" \
        python:3.11-slim-bookworm@sha256:4b4c524dc3dce996864e030c7bd9c6b0e517597189fee48f48e05b499442444b \
        -euc 'apt-get update && apt-get install --no-install-recommends --yes libvips42 && vips_lib=/usr/lib/x86_64-linux-gnu/libvips.so.42 && { printf "%s\\n" "$vips_lib"; ldd "$vips_lib" | awk "{if (\\$3 ~ /^\\//) print \\$3; else if (\\$1 ~ /^\\//) print \\$1}"; } | sort -u | while read -r library; do cp --remove-destination -L "$library" /runtime/; done'
fi
# Prefer an ABI-compatible host library when it has the exact required soname.
# Keep copied files only when the host lacks that soname. This prevents an old
# container copy of glibc or libxml from replacing the host runtime.
mkdir -p "$vips_host_lib_dir"
host_sonames=$(ldconfig -p | awk '{print $1}' | sort -u)
for library in "$vips_runtime_lib_dir"/*; do
    library_name=$(basename "$library")
    if [[ "$library_name" != "libvips.so.42" ]] && grep -Fqx "$library_name" <<<"$host_sonames"; then
        mv "$library" "$vips_host_lib_dir/"
    fi
done

# lxml is intentionally source-only in the project. The host lacks its build
# headers. Install its exact locked wheel only in this local test venv.
uv pip install --python .venv/bin/python --no-config --only-binary=:all: lxml==6.1.0
# Django imports the SAML backend during URL checks. Keep the matching locked
# XMLSec wheel in this local test venv with its bundled compatible libxml.
uv pip install --python .venv/bin/python --no-config --only-binary=:all: xmlsec==1.3.17

# Install the locked development set without rebuilding local C extensions.
# XMLSec cannot use the locked source build because the host lacks libxml2 and
# libxslt headers. The exact locked wheel above supplies the tested runtime.
# Keep installed exceptions with --inexact.
uv sync --locked --group dev --inexact \
    --no-install-package psycopg2 \
    --no-install-package python-ldap \
    --no-install-package lxml \
    --no-install-package xmlsec

# Reassert the test-only exceptions after the locked synchronization.
uv pip install --python .venv/bin/python --no-deps psycopg2-binary==2.9.12
uv pip install --python .venv/bin/python --no-deps "$wheelhouse"/python_ldap-3.4.5-cp311-cp311-linux_x86_64.whl
uv pip install --python .venv/bin/python --no-deps django-auth-ldap==5.3.0
uv pip install --python .venv/bin/python --no-deps django-stubs-ext==6.0.4

# Install local console scripts. Do not install tools globally.
uv pip install --python .venv/bin/python --reinstall --no-deps black==26.3.1 ruff==0.15.12
pnpm install --frozen-lockfile
