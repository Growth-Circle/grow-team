"""Needs-your-attention state: a resolution for each message a user marked
done or dismissed, and a snooze for each item a user put off."""

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


class NeedSnooze(models.Model):
    """An item a user put off until `until`, after which it lists again.
    The item is named by its id in the list, such as "mention:42", so one
    table covers every kind. Rows past `until` are inert and swept when the
    user snoozes again."""

    realm = models.ForeignKey(Realm, on_delete=models.CASCADE)
    # manage.py delete_realm removes UserProfile rows without knowing this
    # table exists, so the reference carries no database-level constraint.
    user = models.ForeignKey(
        UserProfile, on_delete=models.CASCADE, db_constraint=False, related_name="+"
    )
    need_id = models.CharField(max_length=80)
    until = models.DateTimeField()

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["user", "need_id"], name="need_snooze_user_need_unique")
        ]
