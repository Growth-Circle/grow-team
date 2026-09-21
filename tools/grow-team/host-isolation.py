#!/usr/bin/env python3
"""Capture and compare read-only Grow Team host-isolation evidence."""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_BASELINE = (
    PROJECT_ROOT / ".superpowers/sdd/2026-09-21-grow-team-agent-platform/production-baseline.json"
)
DEFAULT_OUTPUT_DIR = (
    PROJECT_ROOT / ".superpowers/sdd/2026-09-21-grow-team-agent-platform/host-isolation"
)
SERVER = "server-gteam"
SSH_TIMEOUT_SECONDS = 30
EXPECTED_UNIT_COUNT = 41
EXPECTED_TIMER_COUNT = 15
EXPECTED_CONTAINER_COUNT = 5
APP_CONTAINER = "grow-team-zulip-1"
HASH = re.compile(r"^[0-9a-f]{64}$")
RunRemote = Callable[[list[str], str], subprocess.CompletedProcess[str]]


def _fail(message: str) -> RuntimeError:
    return RuntimeError(f"Host-isolation evidence is invalid: {message}")


def _require_string(value: object, field: str) -> str:
    if not isinstance(value, str):
        raise _fail(f"{field} is not a string")
    return value


def _unique_records(
    records: Sequence[Mapping[str, object]], key: str, label: str
) -> dict[str, Mapping[str, object]]:
    result: dict[str, Mapping[str, object]] = {}
    for record in records:
        value = _require_string(record.get(key), f"{label}.{key}")
        if value in result:
            raise _fail(f"duplicate {label}: {value}")
        result[value] = record
    return result


def _container(record: Mapping[str, object]) -> dict[str, str]:
    """Normalize legacy Docker ps keys without retaining command or label fields."""
    aliases = {
        "name": ("name", "Names"),
        "id": ("id", "ID"),
        "image": ("image", "Image"),
        "state": ("state", "State"),
        "health": ("health", "HealthStatus"),
    }
    normalized: dict[str, str] = {}
    for target, names in aliases.items():
        value = next((record[name] for name in names if name in record), None)
        normalized[target] = _require_string(value, f"container.{target}")
    return normalized


def _containers(records: object) -> dict[str, dict[str, str]]:
    if not isinstance(records, list):
        raise _fail("containers is not a list")
    result: dict[str, dict[str, str]] = {}
    for raw_record in records:
        if not isinstance(raw_record, Mapping):
            raise _fail("container record is not an object")
        record = _container(raw_record)
        name = record["name"]
        if name in result:
            raise _fail(f"duplicate container: {name}")
        if not name.startswith("grow-team-"):
            raise _fail(f"non-Grow container in evidence: {name}")
        result[name] = record
    return result


def _timers(records: object) -> dict[str, dict[str, str]]:
    if not isinstance(records, list):
        raise _fail("timers is not a list")
    raw: list[Mapping[str, object]] = []
    for record in records:
        if not isinstance(record, Mapping):
            raise _fail("timer record is not an object")
        raw.append(record)
    result: dict[str, dict[str, str]] = {}
    for unit, record in _unique_records(raw, "unit", "timer").items():
        result[unit] = {
            "unit": unit,
            "enabled": _require_string(record.get("enabled"), "timer.enabled"),
            "active": _require_string(record.get("active"), "timer.active"),
        }
    return result


def _units(records: object) -> dict[str, str]:
    if not isinstance(records, Mapping):
        raise _fail("units is not an object")
    result: dict[str, str] = {}
    for path, digest in records.items():
        if not isinstance(path, str) or not path.startswith("/etc/systemd/system/"):
            raise _fail("unit path is invalid")
        value = _require_string(digest, "unit hash")
        if not HASH.fullmatch(value):
            raise _fail(f"unit hash is invalid: {path}")
        result[path] = value
    return result


def _validate_shape(data: object, *, strict_counts: bool) -> dict[str, object]:
    if not isinstance(data, Mapping):
        raise _fail("top-level JSON is not an object")
    units = _units(data.get("units"))
    timers = _timers(data.get("timers"))
    containers = _containers(data.get("containers"))
    if strict_counts and (
        len(units) < EXPECTED_UNIT_COUNT
        or len(timers) != EXPECTED_TIMER_COUNT
        or len(containers) != EXPECTED_CONTAINER_COUNT
    ):
        raise _fail("baseline requires at least 41 units, 15 timers, and 5 Grow containers")
    return {
        "captured_at": _require_string(data.get("captured_at"), "captured_at"),
        "units": units,
        "timers": list(timers.values()),
        "containers": list(containers.values()),
    }


def load_baseline(path: Path) -> dict[str, object]:
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as error:
        raise _fail("cannot read baseline") from error
    return _validate_shape(data, strict_counts=True)


def compare(
    baseline: Mapping[str, object],
    snapshot: Mapping[str, object],
    *,
    allow_app_replacement: bool = False,
) -> list[str]:
    """Return every isolation mismatch. An empty list proves only this snapshot contract."""
    base = _validate_shape(baseline, strict_counts=False)
    try:
        current = _validate_shape(snapshot, strict_counts=False)
    except RuntimeError as error:
        return [str(error)]
    errors: list[str] = []
    base_units = _units(base["units"])
    current_units = _units(current["units"])
    if set(base_units) != set(current_units):
        errors.append("unit paths differ")
    errors.extend(
        f"unit hash changed: {path}"
        for path in sorted(set(base_units) & set(current_units))
        if base_units[path] != current_units[path]
    )

    base_timers = _timers(base["timers"])
    current_timers = _timers(current["timers"])
    if set(base_timers) != set(current_timers):
        errors.append("timer units differ")
    errors.extend(
        f"timer state changed: {unit}"
        for unit in sorted(set(base_timers) & set(current_timers))
        if base_timers[unit] != current_timers[unit]
    )

    base_containers = _containers(base["containers"])
    current_containers = _containers(current["containers"])
    if set(base_containers) != set(current_containers):
        errors.append("container names differ")
    for name in sorted(set(base_containers) & set(current_containers)):
        before, after = base_containers[name], current_containers[name]
        if after["state"] != "running":
            errors.append(f"{name} is not running")
        if after["health"] not in {"healthy", "none"}:
            errors.append(f"{name} is unhealthy")
        replaced = before["id"] != after["id"] or before["image"] != after["image"]
        if replaced and not (allow_app_replacement and name == APP_CONTAINER):
            errors.append(f"{name} was replaced")
        if name == APP_CONTAINER and after["health"] != "healthy":
            errors.append(f"{name} is unhealthy")
        elif not replaced and before["health"] != after["health"]:
            errors.append(f"{name} health changed")
    return errors


def discovery_script(systemd_root: str = "/etc/systemd/system") -> str:
    """Generate the checked Bash discovery function used by the remote collector."""
    root = shlex.quote(systemd_root)
    override_pattern = shlex.quote(f"{systemd_root}/hermestrading*.d/*")
    return f"""discover_hermes_paths() {{
  find {root} -mindepth 1 \\
    \\( -type f -o -type l \\) \\
    \\( -name 'hermestrading*' -o -path {override_pattern} \\) \\
    -print | LC_ALL=C sort
}}
"""


def remote_script(baseline: Mapping[str, object]) -> str:
    """Return fixed remote read-only discovery commands for current Hermes records."""
    _validate_shape(baseline, strict_counts=False)
    return (
        """set -euo pipefail
docker() {
  sudo -n env -u DOCKER_CONTEXT DOCKER_CONFIG=/opt/grow-team/lib/docker /opt/grow-team/bin/docker --host=unix:///run/grow-team-docker/docker.sock \"$@\"
}
"""
        + discovery_script()
        + """hermes_paths=$(discover_hermes_paths)
timer_names=$(printf '%s\\n' \"$hermes_paths\" | sed -n 's#.*/\\(hermestrading[^/]*\\.timer\\)$#\\1#p' | LC_ALL=C sort -u)
while IFS= read -r path; do
  digest=$(sha256sum -- \"$path\")
  set -- $digest
  printf 'UNIT\\t%s\\t%s\\n' \"$path\" \"$1\"
done <<< \"$hermes_paths\"
while IFS= read -r unit; do
  enabled=$(systemctl is-enabled \"$unit\" 2>/dev/null || true)
  active=$(systemctl is-active \"$unit\" 2>/dev/null || true)
  printf 'TIMER\\t%s\\t%s\\t%s\\n' \"$unit\" \"$enabled\" \"$active\"
done <<< \"$timer_names\"
docker ps -a --format '{{.Names}}\\t{{.ID}}\\t{{.Image}}\\t{{.State}}' | while IFS='\t' read -r name id image state; do
  case \"$name\" in
    grow-team-*)
      health=$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' \"$name\")
      printf 'CONTAINER\\t%s\\t%s\\t%s\\t%s\\t%s\\n' \"$name\" \"$id\" \"$image\" \"$state\" \"$health\"
      ;;
  esac
done
"""
    )


def _default_run_remote(command: list[str], source: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        input=source,
        text=True,
        capture_output=True,
        timeout=SSH_TIMEOUT_SECONDS,
        check=False,
    )


def _parse_remote_output(output: str) -> dict[str, object]:
    if not output.strip():
        raise _fail("remote output is empty")
    units: dict[str, str] = {}
    timers: list[dict[str, str]] = []
    containers: list[dict[str, str]] = []
    for line in output.splitlines():
        fields = line.split("\t")
        if fields[0] == "UNIT" and len(fields) == 3:
            if fields[1] in units:
                raise _fail(f"duplicate unit: {fields[1]}")
            units[fields[1]] = fields[2]
        elif fields[0] == "TIMER" and len(fields) == 4:
            timers.append({"unit": fields[1], "enabled": fields[2], "active": fields[3]})
        elif fields[0] == "CONTAINER" and len(fields) == 6:
            containers.append(
                {
                    "name": fields[1],
                    "id": fields[2],
                    "image": fields[3],
                    "state": fields[4],
                    "health": fields[5],
                }
            )
        else:
            raise _fail("remote output format is invalid")
    return {
        "captured_at": datetime.now(UTC).isoformat(),
        "units": units,
        "timers": timers,
        "containers": containers,
    }


def capture(
    baseline: Mapping[str, object], output_dir: Path, *, run_remote: RunRemote = _default_run_remote
) -> Path:
    """Collect remote read-only evidence and write a newly created local JSON file."""
    command = [
        "ssh",
        "-o",
        "BatchMode=yes",
        "-T",
        "-o",
        "ConnectTimeout=10",
        "-o",
        "ServerAliveInterval=5",
        "-o",
        "ServerAliveCountMax=1",
        SERVER,
        "bash",
        "-s",
    ]
    try:
        result = run_remote(command, remote_script(baseline))
    except subprocess.TimeoutExpired as error:
        raise RuntimeError("remote collection timed out") from error
    if result.returncode != 0:
        raise RuntimeError("remote collection failed")
    snapshot = _parse_remote_output(result.stdout)
    _validate_shape(snapshot, strict_counts=False)
    if output_dir.exists():
        directory_status = output_dir.lstat()
        if (
            not output_dir.is_dir()
            or output_dir.is_symlink()
            or directory_status.st_uid != os.geteuid()
            or directory_status.st_mode & 0o077
        ):
            raise RuntimeError("host-isolation output directory is not private")
    else:
        output_dir.mkdir(mode=0o700, parents=True)
        os.chmod(output_dir, 0o700)
    evidence_path = output_dir / (
        "host-isolation-"
        + datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        + "-"
        + uuid4().hex
        + ".json"
    )
    try:
        descriptor = os.open(
            evidence_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600
        )
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            output.write(json.dumps(snapshot, sort_keys=True, indent=2) + "\n")
            output.flush()
    except OSError as error:
        raise RuntimeError("cannot write new host-isolation evidence") from error
    return evidence_path


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    snapshot_parser = subparsers.add_parser(
        "snapshot", help="capture one read-only server-gteam snapshot"
    )
    snapshot_parser.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE)
    snapshot_parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    compare_parser = subparsers.add_parser(
        "compare", help="compare saved evidence to the production baseline"
    )
    compare_parser.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE)
    compare_parser.add_argument("--snapshot", type=Path, required=True)
    compare_parser.add_argument("--allow-app-replacement", action="store_true")
    args = parser.parse_args(argv)
    try:
        baseline = load_baseline(args.baseline)
        if args.command == "snapshot":
            output = capture(baseline, args.output_dir)
            print(output)
            return 0
        snapshot = _validate_shape(json.loads(args.snapshot.read_text()), strict_counts=True)
        errors = compare(baseline, snapshot, allow_app_replacement=args.allow_app_replacement)
    except (OSError, json.JSONDecodeError, RuntimeError) as error:
        print(str(error), file=sys.stderr)
        return 1
    if errors:
        for mismatch in errors:
            print(mismatch, file=sys.stderr)
        return 1
    print("Host isolation comparison passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
