"""Realtime events for job status, runner status, pairing, and room meta.

Every sender here queues its event on commit and with `robust=True`, so one
sender's failure can never cost a sibling `on_commit` hook its own event, and
a caller inside `agent_transaction()` never leaks an event for a write the
database later rolls back.
"""

from collections.abc import Iterable
from datetime import date
from typing import Any, Literal, Protocol

import orjson
from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.utils.timezone import now

from zerver.lib.agent_context import require_job_access
from zerver.lib.agent_policy import AgentAccessDenied
from zerver.lib.agent_presence import observed_runner_status
from zerver.lib.exceptions import JsonableError
from zerver.lib.streams import can_access_stream_metadata_user_ids
from zerver.lib.user_groups import get_recursive_group_members
from zerver.models import Realm, Stream, UserProfile, agents
from zerver.models.users import active_user_ids
from zerver.tornado.django_api import send_event_rollback_unsafe

PairingState = Literal["pending", "approved", "denied", "expired"]


def _send_agent_event(realm: Realm, event: dict[str, Any], users: Iterable[int]) -> None:
    """Like `send_event_on_commit`, but `robust=True`: an earlier on_commit
    hook's failure in the same transaction must never cost this event its
    own delivery (RL-4)."""
    if not settings.USING_RABBITMQ:
        # Same round trip `send_event_on_commit` does: catches a
        # non-JSON-safe value here instead of inside the commit hook.
        event = orjson.loads(orjson.dumps(event))
    transaction.on_commit(lambda: send_event_rollback_unsafe(realm, event, users), robust=True)


def _job_stream_id(job: agents.AgentJob) -> int | None:
    """None for a DM job. Read from the settled audience, not the request
    scope, so a job whose route changed keeps reporting where it settled."""
    binding = job.conversation.audience_binding
    stream_id = binding.get("stream_id") if isinstance(binding, dict) else None
    return stream_id if isinstance(stream_id, int) else None


def _job_review_grant_user_ids(job: agents.AgentJob) -> set[int]:
    grants = agents.AgentGrant.objects.filter(
        realm_id=job.realm_id,
        target_kind="profile",
        profile_id=job.profile_id,
        revoked_at__isnull=True,
    ).filter(Q(expires_at__isnull=True) | Q(expires_at__gt=now()))
    user_ids: set[int] = set()
    for grant in grants:
        if "job.review" not in grant.actions:
            continue
        if grant.principal_user_id is not None:
            user_ids.add(grant.principal_user_id)
        elif grant.principal_group_id is not None:
            user_ids.update(
                get_recursive_group_members(grant.principal_group_id).values_list("id", flat=True)
            )
    return user_ids


def _job_audience(job: agents.AgentJob) -> list[int]:
    """The requester, the agent's owner, and every principal with a
    `job.review` grant whose read access to this exact job still holds.
    `require_job_access` is the same gate `GET /agent/jobs/{id}` enforces,
    so a grant scoped to a different room or repository, or a user outside
    the job's message audience, never receives the event even if a
    broader `job.review` grant exists."""
    candidate_ids = {job.requester_id, job.profile.owner_id} | _job_review_grant_user_ids(job)
    candidates = UserProfile.objects.filter(id__in=candidate_ids, is_active=True)
    audience: list[int] = []
    for candidate in candidates:
        try:
            require_job_access(candidate, job)
        except (AgentAccessDenied, JsonableError):
            continue
        audience.append(candidate.id)
    return audience


def send_agent_job_event(job: agents.AgentJob) -> None:
    # Local import: zerver.actions.agent_jobs imports this module, so a
    # module-level import here would form a cycle.
    from zerver.actions.agent_jobs import job_reason_code

    event = {
        "type": "agent_job",
        "op": "update",
        "job_id": str(job.id),
        "status": job.status,
        "phase": job.phase,
        "version": job.version,
        "profile_id": str(job.profile_id),
        "stream_id": _job_stream_id(job),
        "reason_code": job_reason_code(job),
    }
    _send_agent_event(job.realm, event, _job_audience(job))


def _runner_audience(runner: agents.AgentRunner) -> list[int]:
    """The runner's owner, plus the realm's Owner and Admin users."""
    return list(
        UserProfile.objects.filter(
            Q(id=runner.owner_id)
            | Q(role__in=[UserProfile.ROLE_REALM_OWNER, UserProfile.ROLE_REALM_ADMINISTRATOR]),
            realm_id=runner.realm_id,
            is_active=True,
        ).values_list("id", flat=True)
    )


def send_agent_runner_event(
    runner: agents.AgentRunner,
    *,
    notify: Literal["owner_offline", "admins_offline", "owner_stale"] | None = None,
) -> None:
    # notify: reserved for the offline/stale reminder. A later change reads
    # it here to queue the push notice; this event only carries the value
    # through for that later caller.
    event = {
        "type": "agent_runner",
        "op": "update",
        "runner_id": str(runner.id),
        # The same projection GET /agent/runners returns, not the raw
        # column: a silent runner keeps a stale "online" column value long
        # after its heartbeat lapses (agent_presence.HEARTBEAT_STALE_AFTER).
        "status": observed_runner_status(runner),
        "last_heartbeat_at": (
            None if runner.last_heartbeat_at is None else runner.last_heartbeat_at.isoformat()
        ),
    }
    _send_agent_event(runner.realm, event, _runner_audience(runner))


def send_pairing_event(
    pairing: agents.AgentPairing, state: PairingState, user_ids: list[int]
) -> None:
    """`user_ids` is the caller's call: a pairing has no tenant until it is
    approved, so the audience only ever grows once someone claims it."""
    realm = pairing.realm
    if realm is None or not user_ids:
        return
    event = {
        "type": "agent_runner",
        "op": "pairing",
        "pairing_id": str(pairing.id),
        "state": state,
        "device_name": pairing.device_name,
    }
    _send_agent_event(realm, event, user_ids)


class RoomMetaLike(Protocol):
    """The subset of the room-meta model this module needs, as a `Protocol`
    rather than an import: that model is not defined on this branch yet."""

    stream_id: int
    due_date: date | None
    summary_enabled: bool


def send_room_meta_event(room_meta: RoomMetaLike) -> None:
    stream = Stream.objects.get(id=room_meta.stream_id)
    event = {
        "type": "room_meta",
        "op": "update",
        "stream_id": room_meta.stream_id,
        "due_date": None if room_meta.due_date is None else room_meta.due_date.isoformat(),
        "summary_enabled": room_meta.summary_enabled,
    }
    _send_agent_event(stream.realm, event, can_access_stream_metadata_user_ids(stream))


def send_realm_permissions_event(realm: Realm) -> None:
    """No content: every recipient re-fetches `GET /json/realm/permissions`."""
    event = {"type": "realm_permissions", "op": "update"}
    _send_agent_event(realm, event, active_user_ids(realm.id))


def send_agent_realm_settings_event(realm: Realm, changed: dict[str, Any]) -> None:
    """`changed` carries only the `GET /json/agent/realm-settings` fields that
    changed; the field set is owned by that endpoint, so its value types
    vary by key."""
    event = {"type": "agent_realm_settings", "op": "update", "data": changed}
    _send_agent_event(realm, event, active_user_ids(realm.id))
