#!/usr/bin/env bash
set -euo pipefail
umask 077

exec 9>/run/grow-team-backup.lock
flock --nonblock 9
compose=/opt/grow-team/deploy/compose.sh
mkdir -p /var/backups/grow-team
backup_dir=$(mktemp -d "/var/backups/grow-team/$(date -u +%Y%m%dT%H%M%SZ)-zulip.XXXXXXXX")

# The container shell expands these variables.
# shellcheck disable=SC2016
"$compose" exec -T --interactive=false --user zulip zulip sh -c \
  'set -eu
   umask 077
   staging=$(mktemp -d /tmp/grow-team-backup.XXXXXXXX)
   trap '\''rm -f -- "$staging/zulip.tar.gz"; rmdir -- "$staging"'\'' EXIT
   /home/zulip/deployments/current/manage.py backup --output="$staging/zulip.tar.gz" >&2
   cat "$staging/zulip.tar.gz"' > "$backup_dir/zulip.tar.gz.partial"
mv -- "$backup_dir/zulip.tar.gz.partial" "$backup_dir/zulip.tar.gz"
tar -C / -czf "$backup_dir/operations.tar.gz" \
  etc/grow-team opt/grow-team/deploy \
  etc/systemd/system/grow-team.service \
  etc/systemd/system/grow-team.slice \
  etc/systemd/system/grow-team-docker.service \
  etc/systemd/system/grow-team-cloudflared.service \
  etc/systemd/system/grow-team-ai-tunnel.service \
  etc/systemd/system/grow-team-backup.service \
  etc/systemd/system/grow-team-backup.timer \
  etc/systemd/system/grow-team-agent-reconcile.service \
  etc/systemd/system/grow-team-agent-reconcile.timer
chmod 600 "$backup_dir"/*.tar.gz
tar -tzf "$backup_dir/zulip.tar.gz" > "$backup_dir/files.txt"
(
  cd "$backup_dir"
  sha256sum zulip.tar.gz operations.tar.gz > SHA256SUMS
  sha256sum --check SHA256SUMS
)
printf 'Backup: %s\n' "$backup_dir"
