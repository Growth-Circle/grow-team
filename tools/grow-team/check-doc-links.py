#!/usr/bin/env python3
"""Check that relative Markdown links under a docs tree point at real files.

Usage: tools/grow-team/check-doc-links.py [root]

`root` defaults to `internals/docs`. The script reads every `.md` file
under `root`, resolves each inline link's path against the linking
file's own directory, and reports any link whose target file is
missing. It ignores external links (`http(s)://`, `mailto:`) and
anchor-only links (`#section`).
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

LINK_RE = re.compile(r"\]\(([^)]+)\)")
EXTERNAL_SCHEMES = ("http://", "https://", "mailto:")


def target_path(md_file: Path, link: str) -> Path | None:
    """Resolve a link's file part relative to `md_file`, or None to skip it."""
    link = link.strip()
    if not link or link.startswith(EXTERNAL_SCHEMES) or link.startswith("#"):
        return None
    file_part = link.split("#", 1)[0].strip()
    if not file_part:
        return None
    return (md_file.parent / file_part).resolve()


def check(root: Path) -> list[str]:
    """Return one message per broken link found under `root`."""
    broken = []
    for md_file in sorted(root.rglob("*.md")):
        text = md_file.read_text(encoding="utf-8")
        for lineno, line in enumerate(text.splitlines(), start=1):
            for match in LINK_RE.finditer(line):
                target = target_path(md_file, match.group(1))
                if target is not None and not target.exists():
                    broken.append(f"{md_file}:{lineno}: broken link -> {match.group(1)}")
    return broken


def main() -> int:
    root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("internals/docs")
    if not root.is_dir():
        print(f"check-doc-links: no such directory: {root}", file=sys.stderr)
        return 2
    broken = check(root)
    if broken:
        print(f"{len(broken)} link putus:")
        for line in broken:
            print(f"  {line}")
        return 1
    print("0 link putus.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
