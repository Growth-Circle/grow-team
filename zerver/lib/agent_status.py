"""The Status panel of one agent (V3): what it is working on, what waits on
the reader, and what the reader decided in the last week.

The panel only reads. Every job it lists passes require_job_access for the
reader, the same rule as list_jobs and list_needs, so the panel never shows
a job the reader could not open on their own.
"""

from datetime import timedelta
from typing import TypedDict
from uuid import UUID

from django.core.exceptions import ObjectDoesNotExist
from django.utils.timezone import now as timezone_now

from zerver.actions.agent_jobs import job_title
from zerver.lib import needs
from zerver.lib.agent_context import agent_transaction, ensure_budget, require_job_access
from zerver.lib.exceptions import JsonableError
from zerver.models import UserProfile, agents

ITEM_LIMIT = 20
# A job the reader cannot open still takes a row from the scan, so the scan
# reads more rows than the list shows. list_jobs reads 100 for the same reason.
SCAN_LIMIT = 100
DECIDED_WINDOW = timedelta(days=7)
IN_PROGRESS_STATUSES = [
    "queued",
    "running",
    "verifying",
    "waiting_for_input",
    "waiting_for_approval",
]


class StatusItem(TypedDict):
    id: str
    # "job", "approval" or "decision".
    kind: str
    title: str
    # The job's status. Null for something decided, which has an outcome.
    status: str | None
    # "approved", "rejected" or "answered" for something decided, else null.
    outcome: str | None
    # What the reader answered to a decision, else null.
    detail: str | None
    time: str
    job_id: str | None
    message_id: int | None
    stream_id: int | None
    topic: str | None


class AgentStatus(TypedDict):
    in_progress: list[StatusItem]
    waiting_on_you: list[StatusItem]
    decided: list[StatusItem]


def _in_progress_item(job: agents.AgentJob) -> StatusItem:
    scope = job.conversation.scope
    return {
        "id": f"job:{job.id}",
        "kind": "job",
        "title": job_title(job),
        "status": job.status,
        "outcome": None,
        "detail": None,
        "time": job.created_at.isoformat(),
        "job_id": str(job.id),
        "message_id": job.source_message_id,
        "stream_id": scope.get("stream_id"),
        "topic": scope.get("topic"),
    }


def _need_item(item: needs.NeedItem, message_id: int | None, *, decided: bool) -> StatusItem:
    if decided:
        resolved_action = item["resolved_action"]
        outcome = (
            "answered"
            if item["kind"] == "decision"
            else ("rejected" if resolved_action == "rejected" else "approved")
        )
        status = None
        detail = resolved_action if item["kind"] == "decision" else None
        time = item["resolved_at"] or item["time"]
    else:
        outcome = None
        detail = None
        status = "waiting_for_approval" if item["kind"] == "approval" else "waiting_for_input"
        time = item["time"]
    return {
        "id": item["id"],
        "kind": item["kind"],
        "title": item["text"],
        "status": status,
        "outcome": outcome,
        "detail": detail,
        "time": time,
        "job_id": item["job_id"],
        "message_id": message_id,
        "stream_id": item["stream_id"],
        "topic": item["topic"],
    }


def _jobs_by_id(job_ids: set[UUID]) -> dict[UUID, agents.AgentJob]:
    return {
        job.id: job
        for job in agents.AgentJob.objects.filter(id__in=job_ids).select_related(
            "profile", "runner", "repository", "source_message", "conversation"
        )
    }


def _can_open(actor: UserProfile, job: agents.AgentJob) -> bool:
    try:
        require_job_access(actor, job)
    except (JsonableError, ObjectDoesNotExist, ValueError):
        return False
    return True


def get_agent_status(actor: UserProfile, profile: agents.AgentProfile) -> AgentStatus:
    with agent_transaction(read_only=True):
        in_progress: list[StatusItem] = []
        for job in (
            agents.AgentJob.objects.filter(
                realm=actor.realm, profile=profile, status__in=IN_PROGRESS_STATUSES
            )
            .select_related("runner", "repository", "source_message", "conversation", "profile")
            .order_by("-created_at", "id")[:SCAN_LIMIT]
        ):
            if len(in_progress) >= ITEM_LIMIT:
                break
            ensure_budget()
            if _can_open(actor, job):
                in_progress.append(_in_progress_item(job))

        waiting, decided_items = needs.list_agent_needs(
            actor,
            profile,
            decided_since=timezone_now() - DECIDED_WINDOW,
            scan_limit=SCAN_LIMIT,
            item_limit=ITEM_LIMIT,
        )
        jobs = _jobs_by_id(
            {UUID(item["job_id"]) for item in waiting + decided_items if item["job_id"]}
        )
        decided: list[StatusItem] = []
        for item in decided_items:
            if len(decided) >= ITEM_LIMIT:
                break
            decided_job = jobs.get(UUID(item["job_id"])) if item["job_id"] else None
            if decided_job is not None and _can_open(actor, decided_job):
                decided.append(_need_item(item, decided_job.source_message_id, decided=True))
    waiting_on_you = []
    for item in waiting:
        waiting_job = jobs.get(UUID(item["job_id"])) if item["job_id"] else None
        source_message_id = waiting_job.source_message_id if waiting_job is not None else None
        waiting_on_you.append(_need_item(item, source_message_id, decided=False))
    return {"in_progress": in_progress, "waiting_on_you": waiting_on_you, "decided": decided}
