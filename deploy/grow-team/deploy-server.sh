#!/usr/bin/env bash
# Build and swap the Grow Team image on server-gteam. Run as hermesadmin with sudo.
# Usage: deploy-server.sh FULL_SHA TAG TARBALL
#        deploy-server.sh prune-builds KEEP_DIR
set -euo pipefail

# Keep KEEP_DIR plus the most recently modified other build, which is the
# previous release that a rollback needs. Only direct child directories are
# removed; find does not follow symlinks, so nothing outside is touched.
prune_builds() {
    local dir=${BUILDS_DIR:-/opt/grow-team/builds} keep=$1 old
    sudo find "$dir" -mindepth 1 -maxdepth 1 -type d ! -path "$keep" -printf '%T@ %p\0' |
        sort -z -rn | tail -z -n +2 | cut -z -d' ' -f2- |
        while IFS= read -r -d '' old; do
            echo "remove $old"
            sudo rm -rf -- "$old"
        done
}
if [ "${1:-}" = prune-builds ]; then
    prune_builds "$2"
    exit 0
fi

sha=$1
tag=$2
tarball=$3
short=${sha:0:10}
build=/opt/grow-team/builds/$short
dock=(sudo env DOCKER_HOST=unix:///run/grow-team-docker/docker.sock DOCKER_CONFIG=/opt/grow-team/lib/docker /opt/grow-team/bin/docker)
compose=(sudo /opt/grow-team/deploy/compose.sh)
old_tag=$(sudo grep -oE 'grow-team/server:[^[:space:]]+' /opt/grow-team/deploy/compose.override.yaml | head -1)
echo "old image: $old_tag"

free_gb() { df -BG --output=avail / | tail -1 | tr -dc 0-9; }
guard() { f=$(free_gb); echo "disk free before $1: ${f}G"; if [ "$f" -lt 20 ]; then echo "ABORT: free disk ${f}G < 20G before $1"; exit 3; fi; }
step() { printf '\n== %s\n' "$*"; }

step "extract $tarball into $build"
sudo test ! -e "$build" || { echo "Build directory $build exists. Stop."; exit 1; }
sudo mkdir -p "$build"
sudo tar -xzf "$tarball" -C "$build"
sudo ls "$build"

guard buildx
step "buildx grow-team/server:$tag"
"${dock[@]}" buildx build --network none --progress plain --load \
    --build-arg GROW_TEAM_REVISION="$sha" --tag "grow-team/server:$tag" "$build" 2>&1 | tail -15

step "check_image on the exported image"
"${dock[@]}" run --rm --network none --no-healthcheck --user zulip \
    --entrypoint /home/zulip/deployments/current/.venv/bin/python \
    "grow-team/server:$tag" /opt/grow-team-build/check_image.py 2>&1 | tail -5

step "backup"
sudo /opt/grow-team/deploy/backup.sh 2>&1 | tail -5

step "save rollback data"
sudo cp /opt/grow-team/deploy/compose.override.yaml "/opt/grow-team/deploy/compose.override.yaml.bak-${old_tag#grow-team/server:}"
"${dock[@]}" image inspect --format '{{.Id}}' "$old_tag" | sudo tee "/opt/grow-team/deploy/image-id-before-${tag##*.}.txt"

guard swap
step "swap to $tag"
sudo sed -i "s#${old_tag}#grow-team/server:${tag}#" /opt/grow-team/deploy/compose.override.yaml
sudo grep -n 'grow-team/server' /opt/grow-team/deploy/compose.override.yaml
"${compose[@]}" config --quiet
"${compose[@]}" up -d --no-deps --no-build --wait --wait-timeout 300 zulip 2>&1 | tail -5

step "verify"
"${compose[@]}" ps --format '{{.Name}} {{.Image}} {{.Status}}' | grep zulip-1
"${compose[@]}" logs --no-color --since 10m zulip 2>/dev/null | grep -iE 'Applying|No migrations|migrations completed|Traceback|Error' | tail -15
"${compose[@]}" exec -T zulip supervisorctl status | awk '{print $2}' | sort | uniq -c
curl -s -o /dev/null -w 'login %{http_code}\n' https://team.growc.id/login/

step "prune old builds"
prune_builds "$build"
echo "disk free after prune: $(free_gb)G"
