"""Calendar meetings synced in, with the Fathom summary attached once the
fathom-zulip integration finishes transcribing it."""

from django.db import models
from django.utils.timezone import now as timezone_now

from zerver.models.realms import Realm
from zerver.models.streams import Stream


class Meeting(models.Model):
    realm = models.ForeignKey(Realm, on_delete=models.CASCADE)
    provider = models.CharField(max_length=20, default="google_calendar")
    external_id = models.CharField(max_length=255)
    title = models.CharField(max_length=255, default="")
    starts_at = models.DateTimeField()
    ends_at = models.DateTimeField(null=True, default=None)
    platform = models.CharField(max_length=40, default="")
    join_url = models.URLField(max_length=2048, default="")
    recording_url = models.URLField(max_length=2048, default="")
    summary = models.TextField(default="")
    action_items = models.JSONField(default=list)
    stream = models.ForeignKey(Stream, on_delete=models.SET_NULL, null=True)
    topic = models.TextField(default="")
    fathom_join = models.BooleanField(default=False)
    created_task_ids = models.JSONField(default=list)
    done = models.BooleanField(default=False)
    updated_at = models.DateTimeField(default=timezone_now)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["realm", "provider", "external_id"],
                name="meeting_realm_provider_external_unique",
            ),
        ]
