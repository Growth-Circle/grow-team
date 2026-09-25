"""Web Push subscriptions: one row per browser endpoint a user registered."""

from django.db import models
from django.utils.timezone import now as timezone_now

from zerver.models.realms import Realm
from zerver.models.users import UserProfile


class WebPushSubscription(models.Model):
    # manage.py delete_realm removes UserProfile rows outright, and an old
    # image does not know this table exists, so this reference carries no
    # database-level constraint.
    user = models.ForeignKey(
        UserProfile,
        on_delete=models.CASCADE,
        db_constraint=False,
        related_name="web_push_subscriptions",
    )
    realm = models.ForeignKey(Realm, on_delete=models.CASCADE)
    endpoint = models.URLField(max_length=2048)
    p256dh = models.CharField(max_length=255)
    auth = models.CharField(max_length=255)
    user_agent = models.CharField(max_length=255, default="")
    created_at = models.DateTimeField(default=timezone_now)
    last_success_at = models.DateTimeField(null=True, default=None)
    last_failure_at = models.DateTimeField(null=True, default=None)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["user", "endpoint"], name="web_push_subscription_user_endpoint_unique"
            ),
        ]
