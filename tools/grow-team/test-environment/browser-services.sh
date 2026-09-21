#!/usr/bin/env bash
# Start only the local cache services for browser tests.
set -euo pipefail

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)
environment_dir="$repo_root/tools/grow-team/test-environment"
compose=(docker compose --project-directory "$environment_dir"
    --file "$environment_dir/compose.yaml" --file "$environment_dir/browser-services.yaml")

for dependency in docker python3 ss; do
    command -v "$dependency" >/dev/null || {
        echo "Missing required command: $dependency" >&2
        exit 1
    }
done

[[ -f "$environment_dir/.env" && -f "$repo_root/zproject/dev-secrets.conf" ]] || {
    echo "Run bootstrap.sh before browser-services.sh." >&2
    exit 1
}

for service_and_port in redis:6379 memcached:11211; do
    service=${service_and_port%:*}
    port=${service_and_port#*:}
    container=$("${compose[@]}" ps --quiet "$service")
    if [[ -n "$(ss -ltnH "sport = :$port")" ]]; then
        if [[ -z "$container" ]] || [[ "$(docker port "$container" "$port/tcp")" != "127.0.0.1:$port" ]]; then
            echo "Port 127.0.0.1:$port belongs to another service. Stop here." >&2
            exit 1
        fi
    fi
done

# Use the optional password from this isolated test environment.
python3 - "$repo_root" <<'PY'
import configparser
import os
import re
import sys
from pathlib import Path

repo = Path(sys.argv[1])
secrets = configparser.ConfigParser(interpolation=None)
secrets.read(repo / "zproject/dev-secrets.conf")
password = secrets.get("secrets", "redis_password", fallback=None)
if password is not None and re.fullmatch(r"[A-Za-z0-9+/_=-]{16,}", password) is None:
    raise SystemExit("The generated Redis password has an unexpected format.")
state = repo / "tools/grow-team/test-environment/.state"
state.mkdir(mode=0o700, exist_ok=True)
config = state / "redis.conf"
content = (
    "bind 0.0.0.0\nport 6379\n"
    f"protected-mode {'yes' if password is not None else 'no'}\n"
    'save ""\nappendonly no\nmaxmemory 128mb\nmaxmemory-policy allkeys-lru\n'
)
if password is not None:
    content += f"requirepass {password}\n"
if config.exists():
    if config.read_text() != content:
        raise SystemExit("Redis test configuration changed. Inspect it before restarting the service.")
else:
    descriptor = os.open(config, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w") as output:
        output.write(content)
PY

"${compose[@]}" config --quiet
"${compose[@]}" up --detach --wait --no-deps redis memcached
echo "Browser cache services are ready on loopback. The database was not changed."
