"""The "Send a test notification" check of the notification settings: what
this server knows about the phone channel. The browser channel is only known
to the browser, so the web app checks that one itself."""

import logging

from django.http import HttpRequest, HttpResponse
from django.utils.translation import gettext as _

from zerver.decorator import human_users_only
from zerver.lib.exceptions import JsonableError
from zerver.lib.push_notifications import (
    send_e2ee_test_push_notification,
    send_test_push_notification,
)
from zerver.lib.remote_server import PushNotificationBouncerError
from zerver.lib.response import json_success
from zerver.lib.typed_endpoint import typed_endpoint_without_parameters
from zerver.models import Device, PushDeviceToken, UserProfile

logger = logging.getLogger(__name__)


@human_users_only
@typed_endpoint_without_parameters
def check_mobile_push(request: HttpRequest, user_profile: UserProfile) -> HttpResponse:
    """Send a test push to every device of the user, and say what stopped it
    when none was sent. Both kinds of device are tried: one that registered
    a push token the old way and one that registered a push key."""
    push_service_configured = user_profile.realm.push_notifications_enabled
    legacy_devices = list(PushDeviceToken.objects.filter(user=user_profile))
    # Uses 'zerver_device_user_push_token_id_idx' index.
    push_devices = Device.objects.filter(user=user_profile, push_token_id__isnull=False)
    device_count = len(legacy_devices) + len(push_devices)

    sent = False
    error: str | None = None
    if push_service_configured and device_count > 0:
        try:
            if legacy_devices:
                send_test_push_notification(user_profile, legacy_devices)
            if push_devices:
                send_e2ee_test_push_notification(user_profile, push_devices)
            sent = True
        except JsonableError as e:
            error = e.msg
        except PushNotificationBouncerError:
            logger.exception("Test push for user %s was refused by the bouncer.", user_profile.id)
            error = _("The push service refused the request.")

    return json_success(
        request,
        data={
            "push_service_configured": push_service_configured,
            "device_count": device_count,
            "sent": sent,
            "error": error,
        },
    )
