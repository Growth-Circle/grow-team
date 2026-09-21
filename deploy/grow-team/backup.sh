#!/usr/bin/env bash
set -euo pipefail
umask 077

exec 9>/run/grow-team-backup.lock
flock --nonblock 9
compose=/opt/grow-team/deploy/compose.sh
backup_dir=/var/backups/grow-team/$(date -u +%Y%m%dT%H%M%SZ)-zulip
mkdir -p "$backup_dir"

"$compose" exec -T --user zulip zulip sh -c \
  'umask 077; /home/zulip/deployments/current/manage.py backup --output=/tmp/grow-team-backup.tar.gz'
"$compose" cp zulip:/tmp/grow-team-backup.tar.gz "$backup_dir/zulip.tar.gz"
tar -C / -czf "$backup_dir/operations.tar.gz" \
  etc/grow-team opt/grow-team/deploy \
  etc/systemd/system/grow-team.service \
  etc/systemd/system/grow-team.slice \
  etc/systemd/system/grow-team-docker.service \
  etc/systemd/system/grow-team-cloudflared.service \
  etc/systemd/system/grow-team-ai-tunnel.service \
  etc/systemd/system/grow-team-backup.service \
  etc/systemd/system/grow-team-backup.timer
chmod 600 "$backup_dir"/*.tar.gz
tar -tzf "$backup_dir/zulip.tar.gz" > "$backup_dir/files.txt"
(
  cd "$backup_dir"
  sha256sum zulip.tar.gz operations.tar.gz > SHA256SUMS
  sha256sum --check SHA256SUMS
)
printf 'Backup: %s\n' "$backup_dir"
