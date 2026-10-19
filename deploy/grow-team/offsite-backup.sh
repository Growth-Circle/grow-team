#!/usr/bin/env bash
# Encrypt the newest local Zulip backup with age and copy it to Cloudflare R2.
# Local backups are only read; the script never changes or deletes them.
set -euo pipefail
umask 077

env_file=${OFFSITE_BACKUP_ENV:-/etc/grow-team/offsite-backup.env}
backup_root=${BACKUP_ROOT:-/var/backups/grow-team}
work_root=${OFFSITE_WORK_DIR:-/var/tmp}
lock_file=${OFFSITE_LOCK_FILE:-/run/grow-team-offsite-backup.lock}
prefix=zulip

die() {
    echo "offsite-backup: $*" >&2
    exit 1
}

exec 9>"$lock_file"
flock --nonblock 9 || die "another offsite backup is already running."

for tool in age rclone sha256sum md5sum timeout; do
    command -v "$tool" >/dev/null || die "required tool '$tool' is not installed."
done

[ -f "$env_file" ] || die "$env_file is missing; copy offsite-backup.env.example and fill it in."
[ "$(stat -c %a "$env_file")" = 600 ] || die "$env_file must have mode 0600 (it holds the R2 secret)."
set -a
# shellcheck disable=SC1090
. "$env_file"
set +a
for name in R2_ENDPOINT R2_BUCKET R2_ACCESS_KEY_ID R2_SECRET_ACCESS_KEY AGE_RECIPIENT; do
    [ -n "${!name:-}" ] || die "$name is not set in $env_file."
done
keep=${OFFSITE_KEEP:-14}
case $keep in
    '' | *[!0-9]* | 0) die "OFFSITE_KEEP must be a positive number, got '$keep'." ;;
esac

# rclone reads the remote from the environment, so the secret never touches a config file.
export RCLONE_CONFIG_R2_TYPE=s3
export RCLONE_CONFIG_R2_PROVIDER=Cloudflare
export RCLONE_CONFIG_R2_REGION=auto
export RCLONE_CONFIG_R2_ACL=private
export RCLONE_CONFIG_R2_ENDPOINT=$R2_ENDPOINT
export RCLONE_CONFIG_R2_ACCESS_KEY_ID=$R2_ACCESS_KEY_ID
export RCLONE_CONFIG_R2_SECRET_ACCESS_KEY=$R2_SECRET_ACCESS_KEY
# A bucket-scoped API token cannot create buckets; skip rclone's existence probe.
export RCLONE_CONFIG_R2_NO_CHECK_BUCKET=true
remote=r2:$R2_BUCKET/$prefix
transfer_timeout=${OFFSITE_TRANSFER_TIMEOUT:-3000}

# A backup directory only counts once backup.sh wrote SHA256SUMS, its last step.
newest=
for candidate in "$backup_root"/*-zulip.*; do
    [ -f "$candidate/SHA256SUMS" ] && newest=$candidate
done
[ -n "$newest" ] || die "no complete backup found in $backup_root."
(cd "$newest" && sha256sum --check --quiet SHA256SUMS) ||
    die "local backup $newest fails its own checksums; not uploading it."

name=$(basename "$newest").tar.age
work=$(mktemp -d "$work_root/offsite-backup.XXXXXXXX")
trap 'rm -rf -- "$work"' EXIT

echo "offsite-backup: encrypting $newest"
tar -C "$backup_root" -c -- "$(basename "$newest")" |
    age --encrypt --recipient "$AGE_RECIPIENT" --output "$work/$name" ||
    die "tar or age failed while encrypting $newest (is AGE_RECIPIENT a valid age1... key?)."

local_size=$(stat -c %s "$work/$name")
local_md5=$(md5sum "$work/$name" | cut -d' ' -f1)
(cd "$work" && sha256sum "$name" >"$name.sha256")

echo "offsite-backup: uploading $name ($local_size bytes) to $R2_BUCKET"
timeout "$transfer_timeout" rclone copyto "$work/$name" "$remote/$name" ||
    die "upload of $name failed; local backups are untouched."
timeout 300 rclone copyto "$work/$name.sha256" "$remote/$name.sha256" ||
    die "upload of $name.sha256 failed; local backups are untouched."

remote_size=$(timeout 300 rclone lsf --format s "$remote/$name") ||
    die "could not read back the size of $name from R2."
[ "$remote_size" = "$local_size" ] ||
    die "size mismatch for $name: local $local_size, R2 ${remote_size:-none}."
# Multipart uploads have no MD5; rclone then prints an empty hash and only the size is checked.
remote_md5=$(timeout 300 rclone md5sum "$remote/$name" | awk 'NF >= 2 {print $1}') ||
    die "could not read back the checksum of $name from R2."
if [ -n "$remote_md5" ] && [ "$remote_md5" != "$local_md5" ]; then
    die "checksum mismatch for $name: local $local_md5, R2 $remote_md5."
fi
echo "offsite-backup: verified $name"

listing=$(timeout 300 rclone lsf --files-only "$remote") ||
    die "could not list $R2_BUCKET to prune old copies; upload itself is verified."
mapfile -t copies < <(printf '%s\n' "$listing" | grep -E '\.tar\.age$' | sort)
excess=$((${#copies[@]} - keep))
for ((i = 0; i < excess; i++)); do
    old=${copies[i]}
    [ "$old" != "$name" ] || continue
    echo "offsite-backup: pruning $old"
    timeout 300 rclone deletefile "$remote/$old" || die "could not delete $old from R2."
    timeout 300 rclone deletefile "$remote/$old.sha256" ||
        echo "offsite-backup: warning: $old.sha256 was not deleted (already gone?)." >&2
done
echo "offsite-backup: done, ${#copies[@]} copies in R2 before pruning, keeping $keep."
