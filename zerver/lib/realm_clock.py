"""The clock a workspace keeps, for jobs that run on the workspace's own
morning."""

import zoneinfo
from datetime import datetime

from django.utils.timezone import now as timezone_now

from zerver.lib.timezone import canonicalize_timezone
from zerver.models import AgentRealmSettings, Realm


def realm_timezone_name(realm: Realm) -> str:
    """The name of the timezone that the workspace chose."""
    tz_name = (
        AgentRealmSettings.objects.filter(realm=realm).values_list("timezone", flat=True).first()
    ) or "Asia/Jakarta"
    return canonicalize_timezone(tz_name)


def realm_local_now(realm: Realm) -> datetime:
    """The current time in the timezone that the workspace chose."""
    return timezone_now().astimezone(zoneinfo.ZoneInfo(realm_timezone_name(realm)))
