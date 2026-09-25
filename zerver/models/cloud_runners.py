"""Cloud-provisioned runner instances: Cloudflare Containers (decision D-02;
AWS stays as a fallback choice)."""

from django.db import models
from django.utils.timezone import now as timezone_now

from zerver.models.agents import AgentRunner
from zerver.models.realms import Realm
from zerver.models.users import UserProfile

CLOUD_RUNNER_STATES = ["pending", "starting", "running", "stopping", "stopped", "failed"]
CLOUD_RUNNER_PROVIDERS = ["cloudflare", "aws"]
CLOUD_RUNNER_EGRESS_MODES = ["https_only", "open"]


class CloudRunnerInstance(models.Model):
    realm = models.ForeignKey(Realm, on_delete=models.CASCADE)
    runner = models.ForeignKey(AgentRunner, on_delete=models.SET_NULL, null=True)
    provider = models.CharField(
        max_length=20,
        choices=[(value, value) for value in CLOUD_RUNNER_PROVIDERS],
        default="cloudflare",
    )
    provider_instance_id = models.CharField(max_length=255, default="")
    region = models.CharField(max_length=40, default="")
    size = models.CharField(max_length=40, default="")
    state = models.CharField(
        max_length=20, choices=[(value, value) for value in CLOUD_RUNNER_STATES], default="pending"
    )
    # Outbound network policy for the container (WP46).
    egress = models.CharField(
        max_length=20,
        choices=[(value, value) for value in CLOUD_RUNNER_EGRESS_MODES],
        default="https_only",
        db_default="https_only",
    )
    # manage.py delete_realm removes UserProfile rows outright, and an old
    # image does not know this table exists, so this reference carries no
    # database-level constraint.
    created_by = models.ForeignKey(UserProfile, on_delete=models.PROTECT, db_constraint=False)
    created_at = models.DateTimeField(default=timezone_now)
    stopped_at = models.DateTimeField(null=True, default=None)
    billing_seconds = models.PositiveBigIntegerField(default=0)
    last_error = models.CharField(max_length=500, default="")

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(provider__in=CLOUD_RUNNER_PROVIDERS),
                name="cloud_runner_provider_valid",
            ),
            models.CheckConstraint(
                condition=models.Q(state__in=CLOUD_RUNNER_STATES),
                name="cloud_runner_state_valid",
            ),
            models.CheckConstraint(
                condition=models.Q(egress__in=CLOUD_RUNNER_EGRESS_MODES),
                name="cloud_runner_egress_valid",
            ),
        ]
