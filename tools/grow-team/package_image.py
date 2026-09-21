#!/usr/bin/env python3
"""Package a clean source commit and its existing frontend build."""

import argparse
import json
import subprocess
import tarfile
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    if subprocess.check_output(["git", "status", "--porcelain"], cwd=root):
        raise SystemExit("Commit the source changes before packaging the image.")
    revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
    stats = root / "webpack-stats-production.json"
    if json.loads(stats.read_text())["status"] != "done":
        raise SystemExit("Run the production webpack build first.")

    runtime_dirs = {
        "analytics",
        "api_docs",
        "confirmation",
        "locale",
        "scripts",
        "static",
        "templates",
        "zerver",
        "zproject",
    }
    runtime_files = {"LICENSE", "NOTICE", "README.md", "BRANDING.md", "manage.py", "version.py"}
    excluded = ("zerver/tests/", "templates/corporate/", "templates/zilencer/")
    tracked = subprocess.check_output(["git", "ls-files", "-z"], cwd=root).decode().split("\0")
    build_inputs = {"package.json", "pnpm-lock.yaml", "tools/webpack", "tools/build-help-center"}
    for output, prefixes in (
        (stats, ("web/", "static/", "locale/", "patches/")),
        (root / "starlight_help/dist/index.html", ("starlight_help/",)),
    ):
        stale = [
            name
            for name in tracked
            if name
            and (name.startswith(prefixes) or name in build_inputs)
            and (root / name).stat().st_mtime_ns > output.stat().st_mtime_ns
        ]
        if stale:
            raise SystemExit(f"Rebuild {output.name}; source files are newer: {stale[:5]}")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(args.output, "x:gz", dereference=True) as archive:
        for name in tracked:
            if not name or name.startswith(excluded):
                continue
            path = Path(name)
            if path.parts[0] not in runtime_dirs and name not in runtime_files:
                continue
            if name in {"zproject/dev_settings.py", "zproject/test_settings.py"}:
                continue
            archive.add(root / path, arcname=f"app/{name}", recursive=False)
        for name in (
            "static/webpack-bundles",
            "static/generated/emoji",
            "starlight_help/dist",
            "webpack-stats-production.json",
        ):
            archive.add(root / name, arcname=f"app/{name}")
        archive.add(root / "deploy/grow-team/Dockerfile", arcname="Dockerfile")
        archive.add(root / "deploy/grow-team/assemble_image.py", arcname="assemble_image.py")
        archive.add(root / "deploy/grow-team/check_image.py", arcname="check_image.py")
    print(
        json.dumps(
            {"revision": revision, "archive": str(args.output), "bytes": args.output.stat().st_size}
        )
    )


if __name__ == "__main__":
    main()
