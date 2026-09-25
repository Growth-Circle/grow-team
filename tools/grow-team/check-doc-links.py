#!/usr/bin/env python3
"""Check that relative Markdown links under a docs tree point at real files.

Usage: tools/grow-team/check-doc-links.py [root]

`root` defaults to `internals/docs`. The script reads every `.md` file
under `root`, resolves each inline link's path against the linking
file's own directory, and reports any link whose target file is
missing, or whose `#fragment` matches no heading in the target file.
It ignores external links (`http(s)://`, `mailto:`), links inside a
fenced code block, and it skips the fragment check for a target that
is not a Markdown file.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

LINK_RE = re.compile(r"\]\(([^)]+)\)")
FENCE_RE = re.compile(r"^\s*(```|~~~)")
HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
EXTERNAL_SCHEMES = ("http://", "https://", "mailto:")


def github_slug(heading_text: str) -> str:
    """Turn a heading's text into the anchor GitHub gives it.

    Lowercase, drop everything but letters, digits, spaces, `-`, and
    `_`, then turn each run of spaces into one `-`.
    """
    text = heading_text.lower()
    text = re.sub(r"[^\w\- ]", "", text, flags=re.UNICODE)
    text = re.sub(r"\s+", "-", text.strip())
    return text


def heading_slugs(md_file: Path) -> set[str]:
    """Return every heading anchor a Markdown file defines.

    A duplicate heading text gets GitHub's `-1`, `-2`, ... suffix.
    """
    seen: dict[str, int] = {}
    slugs: set[str] = set()
    in_fence = False
    for line in md_file.read_text(encoding="utf-8").splitlines():
        if FENCE_RE.match(line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        match = HEADING_RE.match(line)
        if not match:
            continue
        slug = github_slug(match.group(2))
        count = seen.get(slug, 0)
        seen[slug] = count + 1
        slugs.add(slug if count == 0 else f"{slug}-{count}")
    return slugs


def target_path(md_file: Path, file_part: str) -> Path:
    """Resolve a link's file part relative to `md_file`."""
    return (md_file.parent / file_part).resolve()


def check(root: Path) -> list[str]:
    """Return one message per broken link found under `root`."""
    broken = []
    for md_file in sorted(root.rglob("*.md")):
        in_fence = False
        slug_cache: dict[Path, set[str]] = {}
        text = md_file.read_text(encoding="utf-8")
        for lineno, line in enumerate(text.splitlines(), start=1):
            if FENCE_RE.match(line):
                in_fence = not in_fence
                continue
            if in_fence:
                continue
            for match in LINK_RE.finditer(line):
                link = match.group(1).strip()
                if not link or link.startswith(EXTERNAL_SCHEMES):
                    continue
                file_part, _, fragment = link.partition("#")
                file_part = file_part.strip()
                if not file_part:
                    # `#fragment` link to a heading in this same file.
                    if md_file.suffix == ".md" and fragment:
                        slugs = slug_cache.setdefault(md_file, heading_slugs(md_file))
                        if fragment not in slugs:
                            broken.append(f"{md_file}:{lineno}: broken anchor -> {link}")
                    continue
                target = target_path(md_file, file_part)
                if not target.exists():
                    broken.append(f"{md_file}:{lineno}: broken link -> {link}")
                    continue
                if fragment and target.suffix == ".md":
                    slugs = slug_cache.setdefault(target, heading_slugs(target))
                    if fragment not in slugs:
                        broken.append(f"{md_file}:{lineno}: broken anchor -> {link}")
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
