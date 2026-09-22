"""Observed runner presence for human-facing agent projections."""

from datetime import timedelta

from django.utils.timezone import now

from zerver.models import agents

HEARTBEAT_STALE_AFTER = timedelta(seconds=90)


def observed_runner_status(runner: agents.AgentRunner) -> str:
    if runner.revoked_at is not None:
        return "revoked"
    if runner.status == "online" and (
        runner.last_heartbeat_at is None
        or runner.last_heartbeat_at <= now() - HEARTBEAT_STALE_AFTER
    ):
        return "unknown"
    return runner.status
