"""Needs-your-attention resolutions: one row per message a user marked
done or dismissed."""

from django.db import models
from django.utils.timezone import now as timezone_now

from zerver.models.messages import Message
from zerver.models.realms import Realm
from zerver.models.users import UserProfile

NEED_RESOLUTION_ACTIONS = ["done", "dismissed"]


class NeedResolution(models.Model):
    realm = models.ForeignKey(Realm, on_delete=models.CASCADE)
    # manage.py delete_realm removes UserProfile rows, and retention removes
    # Message rows, both without knowing this table exists, so neither
    # reference carries a database-level constraint. Read code must handle
    # a user_id or message_id whose row is gone.
    user = models.ForeignKey(
        UserProfile, on_delete=models.CASCADE, db_constraint=False, related_name="+"
    )
    message = models.ForeignKey(
        Message, on_delete=models.CASCADE, db_constraint=False, related_name="+"
    )
    action = models.CharField(
        max_length=20, choices=[(value, value) for value in NEED_RESOLUTION_ACTIONS], default="done"
    )
    resolved_at = models.DateTimeField(default=timezone_now)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["user", "message"], name="need_resolution_user_message_unique"
            ),
            models.CheckConstraint(
                condition=models.Q(action__in=NEED_RESOLUTION_ACTIONS),
                name="need_resolution_action_valid",
            ),
        ]
