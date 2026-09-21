"""Bounded recovery from durable records, independent of feature admission."""

from collections.abc import Callable
from datetime import timedelta

from django.utils.timezone import now

from zerver.actions.agent_jobs import audit, invalidate_approvals, request_stop, transition
from zerver.lib.agent_context import AgentBusy, agent_transaction
from zerver.lib.agent_results import publish_result
from zerver.models import agents


def reconcile_agents(
    *, limit: int = 100, notify: Callable[[dict[str, object]], None] | None = None
) -> dict[str, int]:
    if not 1 <= limit <= 1000:
        raise ValueError("Invalid reconciliation limit.")
    counts = {
        "expired_queues": 0,
        "interrupted": 0,
        "expired_approvals": 0,
        "published": 0,
        "notifications": 0,
        "blocked": 0,
    }
    with agent_transaction():
        queued = (
            agents.AgentJob.objects.select_for_update(skip_locked=True)
            .filter(status="queued", start_deadline__lte=now())
            .order_by("created_at")[:limit]
        )
        for job in queued:
            transition(job, "blocked", reason="start_deadline_expired")
            counts["expired_queues"] += 1
        attempts = (
            agents.AgentAttempt.objects.select_for_update(skip_locked=True)
            .filter(active=True, lease_expires_at__lte=now())
            .exclude(process_state="unknown")
            .order_by("lease_expires_at")[:limit]
        )
        for attempt in attempts:
            job = agents.AgentJob.objects.select_for_update().get(id=attempt.job_id)
            attempt.process_state = "unknown"
            attempt.save(update_fields=["process_state"])
            invalidate_approvals(attempt)
            agents.AgentOperation.objects.filter(attempt=attempt, status="started").update(
                status="outcome_unknown"
            )
            agents.AgentInput.objects.filter(
                delivered_attempt=attempt, delivery_state="delivered"
            ).update(delivery_state="delivery_uncertain")
            transition(job, "interrupted", reason="stop_unconfirmed")
            audit(
                job,
                "attempt.interrupted",
                {"status": "interrupted", "reason": "stop_unconfirmed"},
                attempt=attempt,
            )
            counts["interrupted"] += 1
        expired = agents.AgentApproval.objects.select_for_update(skip_locked=True).filter(
            decision__in=["pending", "approved"], expires_at__lte=now()
        )[:limit]
        for approval in expired:
            approval.decision = "expired"
            approval.save(update_fields=["decision"])
            job = agents.AgentJob.objects.select_for_update().get(id=approval.job_id)
            if approval.attempt.active:
                request_stop(job, approval.attempt, target="blocked", reason="approval_expired")
            counts["expired_approvals"] += 1
    pending = list(
        agents.AgentOutbox.objects.filter(
            status__in=["pending", "processing", "blocked"], next_attempt_at__lte=now()
        )
        .order_by("next_attempt_at", "id")
        .values_list("id", flat=True)[:limit]
    )
    for outbox_id in pending:
        with agent_transaction():
            item = agents.AgentOutbox.objects.select_for_update().get(id=outbox_id)
            if item.status == "delivered" or item.next_attempt_at > now():
                continue
            item.status = "processing"
            item.attempt_count += 1
            item.next_attempt_at = now() + timedelta(
                seconds=min(300, 2 ** min(item.attempt_count, 8))
            )
            item.save(update_fields=["status", "attempt_count", "next_attempt_at"])
            event = {
                "delivery_key": item.delivery_key,
                "event_type": item.event_type,
                "job_id": str(item.job_id),
                "realm_id": item.realm_id,
            }
        delivered = False
        try:
            if item.event_type == "result.publish" and item.job_id is not None:
                publish_result(item.job_id)
                counts["published"] += 1
                delivered = True
            elif notify is not None:
                # External notification is advisory. Runner polling reads the same database.
                notify(event)
                counts["notifications"] += 1
                delivered = True
            elif item.job is not None:
                delivered = (
                    item.job.status != "queued"
                    if item.event_type == "job.wake"
                    else (
                        item.payload_ref is None
                        or not agents.AgentAttempt.objects.filter(
                            id=item.payload_ref, active=True
                        ).exists()
                    )
                )
        except AgentBusy:
            pass
        except ValueError:
            counts["blocked"] += 1
        except Exception:
            # Do not persist arbitrary transport errors, URLs, or credentials.
            counts["blocked"] += 1
        with agent_transaction():
            item = agents.AgentOutbox.objects.select_for_update().get(id=outbox_id)
            if item.status != "delivered":
                item.status = "delivered" if delivered else "pending"
                item.delivered_at = now() if delivered else None
                item.save(update_fields=["status", "delivered_at"])
    return counts
