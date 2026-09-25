"""Per-profile agent stats (spec 06-S1, S2).

Each number comes from one aggregate query over the requested profiles,
run at read time. There is no separate aggregation job at this pilot
scale (map-backend 06-S1/S2).
"""

from collections.abc import Iterable
from datetime import timedelta
from uuid import UUID

from django.db.models import Count, Q
from django.db.models.functions import Coalesce
from django.utils.timezone import now

from zerver.models import agents
from zerver.models.tasks import Task

TASKS_PER_WEEK_WINDOW = timedelta(days=7)
APPROVE_RATE_WINDOW = timedelta(days=30)

# An approved operation changes to "consumed" when it runs, so both count
# as a yes.
_APPROVED_STATES = ["approved", "consumed"]


def profile_stats(profile_ids: Iterable[UUID]) -> dict[UUID, dict[str, object]]:
    """{profile_id: {"tasks_per_week": int, "approve_rate": float | None}}.

    tasks_per_week counts the cards assigned to the agent that were done
    in the last 7 days. approve_rate divides the approvals given by the
    approvals requested in the last 30 days. A profile with no approval
    request gets None, so the client can show "no data" instead of 0%.
    """
    ids = list(profile_ids)
    since_week = now() - TASKS_PER_WEEK_WINDOW
    since_month = now() - APPROVE_RATE_WINDOW

    task_counts = dict(
        Task.objects.filter(completed_at__gte=since_week)
        .filter(Q(agent_profile_id__in=ids) | Q(assignee__agent_profile__id__in=ids))
        .values(profile_key=Coalesce("agent_profile_id", "assignee__agent_profile__id"))
        .annotate(count=Count("id"))
        .values_list("profile_key", "count")
    )

    approvals = {
        profile_id: (approved, total)
        for profile_id, approved, total in agents.AgentApproval.objects.filter(
            job__profile_id__in=ids, created_at__gte=since_month
        )
        .values("job__profile_id")
        .annotate(approved=Count("id", filter=Q(decision__in=_APPROVED_STATES)), total=Count("id"))
        .values_list("job__profile_id", "approved", "total")
    }

    stats: dict[UUID, dict[str, object]] = {}
    for profile_id in ids:
        approved, total = approvals.get(profile_id, (0, 0))
        stats[profile_id] = {
            "tasks_per_week": task_counts.get(profile_id, 0),
            "approve_rate": round(approved / total, 4) if total else None,
        }
    return stats
