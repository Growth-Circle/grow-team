#!/usr/bin/env bash
# Copy generated test assets into this worktree without changing the source worktree.
set -euo pipefail

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)
asset_source=${GROW_TEAM_ASSET_SOURCE:-/home/ramaaditya/Project/.worktrees/grow-team-branding}

[[ -d "$asset_source" ]] || {
    echo "Missing generated asset source: $asset_source" >&2
    exit 1
}

for source_path in \
    "$asset_source/static/generated/emoji" \
    "$asset_source/web/generated/emoji" \
    "$asset_source/web/generated/emoji-styles" \
    "$asset_source/web/generated/supported_browser_regex.ts" \
    "$asset_source/web/generated/pygments_data.json" \
    "$asset_source/web/generated/timezones.json"; do
    [[ -e "$source_path" ]] || {
        echo "Missing generated asset: $source_path" >&2
        exit 1
    }
done

mkdir -p "$repo_root/static/generated" "$repo_root/web/generated"
cp -aL "$asset_source/static/generated/emoji" "$repo_root/static/generated/"
cp -aL "$asset_source/web/generated/emoji" "$repo_root/web/generated/"
cp -aL "$asset_source/web/generated/emoji-styles" "$repo_root/web/generated/"
cp -aL "$asset_source/web/generated/supported_browser_regex.ts" "$repo_root/web/generated/"
cp -aL "$asset_source/web/generated/pygments_data.json" "$repo_root/web/generated/"
cp -aL "$asset_source/web/generated/timezones.json" "$repo_root/web/generated/"

echo "Generated assets are ready in $repo_root."
