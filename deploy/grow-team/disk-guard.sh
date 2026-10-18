#!/usr/bin/env bash
# Keep the shared server-gteam disk from filling up.
# Usage: disk-guard.sh trim-go-cache | check
set -euo pipefail

deploy_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
go_cache=${GO_CACHE_DIR:-/home/hermesadmin/.cache/go-build}
state_dir=${DISK_GUARD_STATE_DIR:-/var/lib/grow-team}
alert_percent=${DISK_ALERT_PERCENT:-80}
# One DM per window while the disk stays full, so owners are not paged hourly.
alert_every_seconds=$((12 * 3600))

trim_go_cache() {
    [ -d "$go_cache" ] || exit 0
    # Other tenants build with this cache at any time. A running build touches
    # the entries it uses, so anything untouched for a day is safe to drop;
    # `go clean -cache` would break builds in progress.
    find "$go_cache" -type f -name '*-[ad]' -mtime +1 -delete
    du -sh "$go_cache"
}

used_percent() {
    df --output=pcent / | tail -1 | tr -dc 0-9
}

check() {
    local used last now
    used=$(used_percent)
    echo "disk used: ${used}%"
    [ "$used" -ge "$alert_percent" ] || exit 0

    mkdir -p "$state_dir"
    now=$(date +%s)
    last=$(cat "$state_dir/disk-alert.last" 2>/dev/null || echo 0)
    if [ $((now - last)) -lt "$alert_every_seconds" ]; then
        echo "alert already sent at $(date -d "@$last" -Is)"
        exit 0
    fi

    local avail
    avail=$(df -BG --output=avail / | tail -1 | tr -dc 0-9)
    "$deploy_dir/compose.sh" exec -T --interactive=false --user zulip \
        --env DISK_ALERT_TEXT="Disk server-gteam terpakai ${used}% (sisa ${avail} GB). Deploy berhenti otomatis di bawah 20 GB. Cek \`sudo du -xh -d1 / | sort -h\`." \
        zulip /home/zulip/deployments/current/manage.py shell -c '
import os
from django.conf import settings
from zerver.actions.message_send import internal_send_private_message
from zerver.models import Realm, UserProfile
from zerver.models.users import get_system_bot

for realm in Realm.objects.filter(deactivated=False).exclude(string_id=settings.SYSTEM_BOT_REALM):
    bot = get_system_bot(settings.NOTIFICATION_BOT, realm.id)
    for owner in UserProfile.objects.filter(
        realm=realm, role=UserProfile.ROLE_REALM_OWNER, is_active=True, is_bot=False
    ):
        internal_send_private_message(bot, owner, os.environ["DISK_ALERT_TEXT"])
'
    echo "$now" > "$state_dir/disk-alert.last"
}

case ${1:-} in
    trim-go-cache) trim_go_cache ;;
    check) check ;;
    *)
        echo "Usage: $0 trim-go-cache | check" >&2
        exit 2
        ;;
esac
