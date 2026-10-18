"""Scheduled routines: start the job of each routine that is due, keep the
history of its runs, and tell its owner when a run does not go well.

A routine runs as a job of its agent profile, and the job's own bot posts
the result, so nothing a routine does is ever sent as a person. Owners
get a direct message from the Notification Bot when something needs them.
"""

import logging
from datetime import datetime, time, timedelta
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from django.conf import settings as django_settings
from django.db import transaction
from django.utils.timezone import now as timezone_now
from django.utils.translation import gettext as _
from django.utils.translation import override as override_language

from zerver.actions.agent_jobs import TERMINAL, agent_language, create_job, require_ready
from zerver.actions.message_send import internal_send_private_message
from zerver.actions.tasks import code_span
from zerver.lib.agent_context import AgentBusy, agent_realm, agent_transaction
from zerver.lib.agent_policy import check_agent_access
from zerver.lib.exceptions import JsonableError
from zerver.lib.realm_clock import realm_local_now
from zerver.lib.role_permissions import has_role_permission
from zerver.lib.room_digests import CONTEXT_MESSAGE_LIMIT, post_agent_message
from zerver.lib.routine_budget import gate
from zerver.lib.routine_events import send_routine_event
from zerver.lib.routine_schedule import Schedule, next_run_after
from zerver.lib.sanji_format import format_date_short
from zerver.lib.streams import access_stream_by_id
from zerver.lib.tasks import realm_task_id_prefix, visible_tasks
from zerver.models import (
    AgentRealmSettings,
    AgentRoutine,
    AgentRoutineRun,
    Message,
    Realm,
    Task,
    TaskBoard,
    UserProfile,
)
from zerver.models.routines import ROUTINE_EXECUTOR_AGENT
from zerver.models.users import get_system_bot

logger = logging.getLogger(__name__)

# A run that comes later than this after its time is not worth doing: a
# morning brief at 15:00 would only confuse. A host that was down for
# longer than this skips the run and records it as missed.
GRACE = timedelta(hours=2)

# One pass of the scheduler starts at most this many routines of a
# workspace. The rest wait for the next minute.
MAX_DUE_PER_PASS = 20

# After a long stop, only this many of the missed runs get a row of their own.
MAX_MISSED_RECORDED = 10

# A routine that was due longer ago than this is not scanned run by run;
# only the occurrences of this window count as missed.
MISSED_SCAN_WINDOW = timedelta(days=7)

# The first run of a routine reads the channel this far back. Later runs
# read from the previous one, but never further back than the cap.
FIRST_CONTEXT_LOOKBACK = timedelta(hours=24)
MAX_CONTEXT_LOOKBACK = timedelta(days=7)

# A routine whose last runs all failed this many times in a row is paused.
FAILURES_BEFORE_PAUSE = 3

MANUAL_RUN_INTERVAL = timedelta(minutes=1)

# The task board summary is for an agent that cannot call a tool yet, so it
# lists only what a morning brief needs, and caps each list.
BOARD_LIST_LIMIT = 8
BOARD_TITLE_LIMIT = 120


def routine_schedule(routine: AgentRoutine) -> Schedule:
    return Schedule(
        pattern_kind=routine.pattern_kind,
        timezone=routine.timezone,
        interval=routine.interval,
        time_of_day=routine.time_of_day,
        weekdays=tuple(routine.weekdays),
        month_day=routine.month_day,
    )


def next_run_from(routine: AgentRoutine, after: datetime) -> datetime:
    return next_run_after(routine_schedule(routine), after)


def _single_line(text: str, limit: int) -> str:
    line = " ".join(text.split())
    return line if len(line) <= limit else line[: limit - 1] + "…"


def board_summary(owner: UserProfile) -> str:
    """The task board as text, as the owner sees it: the open cards that are
    overdue, due today, or blocked. Empty when there is nothing to report.
    Write it in the language of the agent: call inside override_language."""
    board = TaskBoard.objects.filter(realm=owner.realm).order_by("id").first()
    if board is None:
        return ""
    local_now = realm_local_now(owner.realm)
    start_of_today = datetime.combine(local_now.date(), time.min, tzinfo=local_now.tzinfo)
    end_of_today = start_of_today + timedelta(days=1)
    cards = [card for card in visible_tasks(owner, board) if card.completed_at is None]
    prefix = realm_task_id_prefix(owner.realm)

    def describe(cards_in_group: list[Task]) -> list[str]:
        lines = []
        for card in cards_in_group[:BOARD_LIST_LIMIT]:
            line = f"- {prefix}-{card.counter} {_single_line(card.title, BOARD_TITLE_LIMIT)}"
            if card.assignee is not None:
                line += f" ({card.assignee.full_name})"
            if card.due_at is not None:
                due = card.due_at.astimezone(local_now.tzinfo).date()
                line += f" · {format_date_short(due, agent_language(owner.realm_id))}"
            lines.append(line)
        hidden = len(cards_in_group) - BOARD_LIST_LIMIT
        if hidden > 0:
            lines.append(_("And {count} more.").format(count=hidden))
        return lines

    overdue = [c for c in cards if c.due_at is not None and c.due_at < start_of_today]
    due_today = [
        c for c in cards if c.due_at is not None and start_of_today <= c.due_at < end_of_today
    ]
    blocked = [c for c in cards if c.blocked]
    sections = []
    for heading, group in [
        (_("Overdue cards"), overdue),
        (_("Cards due today"), due_today),
        (_("Blocked cards"), blocked),
    ]:
        if group:
            sections.append("\n".join([f"{heading} ({len(group)}):", *describe(group)]))
    if not sections:
        return ""
    return "\n\n".join(
        [
            _("Task board on {date}:").format(
                date=format_date_short(local_now.date(), agent_language(owner.realm_id))
            ),
            *sections,
        ]
    )


def _context_message_ids(routine: AgentRoutine, now: datetime) -> list[int]:
    """The newest messages of the routine's channel since its last run, in
    every topic, oldest first. The messages of the agent itself do not
    count, so a routine does not summarize its own summaries."""
    assert routine.stream is not None
    assert routine.stream.recipient_id is not None
    assert routine.agent_profile is not None
    since = max(routine.last_run_at or now - FIRST_CONTEXT_LOOKBACK, now - MAX_CONTEXT_LOOKBACK)
    ids = list(
        Message.objects.filter(
            realm_id=routine.realm_id,
            recipient_id=routine.stream.recipient_id,
            date_sent__gt=since,
        )
        .exclude(sender_id=routine.agent_profile.bot_user_id)
        .order_by("-id")
        .values_list("id", flat=True)[:CONTEXT_MESSAGE_LIMIT]
    )
    ids.reverse()
    return ids


def _unavailable_reason(routine: AgentRoutine) -> str:
    """Why the routine cannot run right now, as an error code, or an empty
    string when it can. A run never falls back to another agent."""
    profile = routine.agent_profile
    owner = routine.owner
    stream = routine.stream
    assert profile is not None
    assert stream is not None
    if not owner.is_active or owner.is_bot:
        return "owner_unavailable"
    if (
        profile.archived_at is not None
        or profile.desired_state != "enabled"
        or not profile.bot_user.is_active
    ):
        return "agent_unavailable"
    try:
        require_ready(profile)
    except ValueError:
        return "agent_unavailable"
    try:
        if not has_role_permission(owner, "agent_task"):
            return "agent_access"
        check_agent_access(owner, profile, None, None, "profile.use")
    except JsonableError:
        return "agent_access"
    try:
        access_stream_by_id(owner, stream.id)
    except JsonableError:
        return "channel_unavailable"
    return ""


def _reason_text(error_code: str) -> str:
    reasons = {
        "owner_unavailable": _("The owner of the routine is no longer an active member."),
        "agent_unavailable": _("The agent is archived, paused or not ready."),
        "agent_access": _("The owner can no longer use this agent."),
        "channel_unavailable": _("The owner can no longer read this channel."),
        "budget_exceeded": _("The budget for this month is used up."),
        "job_failed": _("The agent's job failed."),
        "job_cancelled": _("The agent's job was cancelled."),
        "job_interrupted": _("The agent's job was interrupted."),
        "job_blocked": _("The agent's job could not start."),
        "job_missing": _("The agent's job is gone."),
        "job_not_created": _("The agent's job could not be started."),
    }
    return reasons.get(error_code, _("Something went wrong."))


def _scheduled_link(realm: Realm) -> str:
    return f"[{_('Open Scheduled')}]({realm.url}/#routines)"


def _notify_owner(routine: AgentRoutine, *lines: str) -> None:
    """Send the owner a direct message from the Notification Bot. The caller
    writes `lines` in the owner's language."""
    sender = get_system_bot(django_settings.NOTIFICATION_BOT, routine.realm_id)
    internal_send_private_message(sender, routine.owner, "\n\n".join(lines))


def _pause(routine: AgentRoutine, reason: str) -> None:
    routine.enabled = False
    routine.paused_reason = reason


def _finish_run(
    routine: AgentRoutine, run: AgentRoutineRun, status: str, error_code: str, now: datetime
) -> None:
    """Close the run as `status`. A run that did not go well tells the owner
    once; three failures in a row also pause the routine. Saves both."""
    run.status = status
    run.error_code = error_code
    run.finished_at = now
    run.save(update_fields=["status", "error_code", "finished_at", "result_message"])
    routine.last_status = status
    paused_now = False
    if status == "succeeded":
        routine.failure_streak = 0
    elif status == "failed":
        routine.failure_streak += 1
        if routine.failure_streak >= FAILURES_BEFORE_PAUSE and routine.enabled:
            _pause(routine, "failures")
            paused_now = True
    elif status == "skipped_budget":
        _pause(routine, "budget")
        paused_now = True
    elif status == "skipped_unavailable":
        _pause(routine, "unavailable")
        paused_now = True
    routine.save(update_fields=["last_status", "failure_streak", "enabled", "paused_reason"])
    if status != "succeeded":
        _send_run_notice(routine, run, paused_now)


def _send_run_notice(routine: AgentRoutine, run: AgentRoutineRun, paused: bool) -> None:
    if not routine.owner.is_active or run.notified_at is not None:
        return
    with override_language(routine.owner.default_language):
        title = code_span(routine.title)
        if run.status == "failed":
            first = _("The routine {title} did not run well.").format(title=title)
        else:
            first = _("The routine {title} did not run.").format(title=title)
        lines = [first, _reason_text(run.error_code)]
        if paused:
            lines.append(_("The routine is paused. Resume it on the Scheduled page."))
        lines.append(_scheduled_link(routine.realm))
    _notify_owner(routine, *lines)
    run.notified_at = timezone_now()
    run.save(update_fields=["notified_at"])


def _send_budget_warning(routine: AgentRoutine, now: datetime) -> None:
    month = now.strftime("%Y-%m")
    if routine.budget_notice_month == month:
        return
    routine.budget_notice_month = month
    routine.save(update_fields=["budget_notice_month"])
    if not routine.owner.is_active:
        return
    with override_language(routine.owner.default_language):
        text = _(
            "The budget for this month is almost used up. The routine {title} keeps running."
        ).format(title=code_span(routine.title))
        link = _scheduled_link(routine.realm)
    _notify_owner(routine, text, link)


def _request_text(routine: AgentRoutine) -> str:
    """The job's request. Its first line is the title of the job's card, so
    it is the routine's title, and the task board summary follows the
    instructions because the agent has no tool to read the board itself."""
    with override_language(agent_language(routine.realm_id)):
        parts = [routine.title, routine.instructions]
        summary = board_summary(routine.owner)
    if summary:
        parts.append(summary)
    return "\n\n".join(part for part in parts if part)


def execute_routine_run(
    routine: AgentRoutine, run: AgentRoutineRun, *, idempotency_key: UUID, now: datetime
) -> None:
    """Start the job of `run`, or close the run with the reason that it
    cannot start. Call inside agent_transaction(), with `run` already saved
    as running. Raises AgentBusy to ask the caller to try again later."""
    assert routine.stream is not None
    assert routine.agent_profile is not None

    reason = _unavailable_reason(routine)
    if reason:
        _finish_run(routine, run, "skipped_unavailable", reason, now)
        return
    state = gate(routine)
    if state == "exceeded":
        _finish_run(routine, run, "skipped_budget", "budget_exceeded", now)
        return
    if state == "warn":
        _send_budget_warning(routine, now)

    profile = routine.agent_profile
    try:
        # A nested transaction: a job that cannot start rolls the source
        # message back with it, and the run is closed as failed.
        with agent_transaction():
            source = post_agent_message(
                profile.bot_user, routine.stream, routine.topic, routine.title, routine.owner
            )
            job = create_job(
                routine.owner,
                profile=profile,
                source=source,
                request=_request_text(routine),
                idempotency_key=idempotency_key,
                job_kind="answer",
                delivery_target="answer",
                trigger_kind="manual",
                context_message_ids=_context_message_ids(routine, now),
            )
    except AgentBusy:
        raise
    except (ValueError, JsonableError) as error:
        # The savepoint rolled back, so nothing was posted. This run fails,
        # and the next occurrence tries again.
        logger.warning("Routine %s could not start its job: %s", routine.id, error)
        _finish_run(routine, run, "failed", "job_not_created", now)
        return
    run.job = job
    run.save(update_fields=["job"])
    routine.last_run_at = now
    routine.last_status = "running"
    routine.save(update_fields=["last_run_at", "last_status"])


def _run_due_routine(routine: AgentRoutine, now: datetime) -> bool:
    """Handle one routine that is due. Moves it to its next occurrence after
    `now` whatever happens, so a host that was down does not run the
    missed occurrences one after the other. Returns whether a job started."""
    assert routine.next_run_at is not None
    schedule = routine_schedule(routine)
    first = routine.next_run_at
    if first < now - MISSED_SCAN_WINDOW:
        first = next_run_after(schedule, now - MISSED_SCAN_WINDOW)
    occurrences = []
    occurrence = first
    while occurrence <= now:
        occurrences.append(occurrence)
        occurrence = next_run_after(schedule, occurrence)
    routine.next_run_at = occurrence
    routine.save(update_fields=["next_run_at"])
    if not occurrences:
        return False

    latest = occurrences[-1]
    runnable = latest if now - latest <= GRACE else None
    missed = [moment for moment in occurrences if moment != runnable][-MAX_MISSED_RECORDED:]
    AgentRoutineRun.objects.bulk_create(
        [
            AgentRoutineRun(
                realm=routine.realm,
                routine=routine,
                scheduled_for=moment,
                started_at=now,
                finished_at=now,
                status="skipped_missed",
            )
            for moment in missed
        ],
        ignore_conflicts=True,
    )
    if runnable is None:
        return False

    run, created = AgentRoutineRun.objects.get_or_create(
        routine=routine,
        scheduled_for=runnable,
        defaults={"realm": routine.realm, "started_at": now, "status": "running"},
    )
    if not created:
        return False
    key = uuid5(NAMESPACE_URL, f"routine:{routine.id}:{runnable.isoformat()}")
    execute_routine_run(routine, run, idempotency_key=key, now=now)
    return run.status == "running"


def run_due_routines(realm: Realm, now: datetime | None = None) -> int:
    """Start the job of every enabled routine of `realm` that is due, and
    return how many started. Call once per realm, inside agent_realm()."""
    now = now or timezone_now()
    if not AgentRealmSettings.objects.filter(realm=realm, enabled=True).exists():
        return 0
    due_ids = list(
        AgentRoutine.objects.filter(
            realm=realm, enabled=True, executor=ROUTINE_EXECUTOR_AGENT, next_run_at__lte=now
        )
        .order_by("next_run_at")
        .values_list("id", flat=True)[:MAX_DUE_PER_PASS]
    )
    started = 0
    for routine_id in due_ids:
        try:
            with agent_transaction():
                # The row is read again under its lock, so two scheduler
                # instances never both act on it.
                routine = (
                    AgentRoutine.objects.select_for_update(
                        skip_locked=True, of=("self",), no_key=True
                    )
                    .select_related("owner", "stream", "agent_profile__bot_user", "realm")
                    .filter(
                        id=routine_id,
                        enabled=True,
                        executor=ROUTINE_EXECUTOR_AGENT,
                        next_run_at__lte=now,
                    )
                    .first()
                )
                if routine is None:
                    continue
                started += _run_due_routine(routine, now)
                send_routine_event(routine, "update")
        except AgentBusy:
            # Another agent write holds the lock. The routine did not move,
            # so the next pass tries again.
            continue
    return started


def run_routine_now(routine: AgentRoutine, now: datetime | None = None) -> AgentRoutineRun:
    """Start one run of the routine now, apart from its schedule. Its next
    run does not change. A routine can run now once a minute."""
    now = now or timezone_now()
    with agent_realm(routine.realm_id), agent_transaction():
        routine = (
            AgentRoutine.objects.select_for_update(of=("self",), no_key=True)
            .select_related("owner", "stream", "agent_profile__bot_user", "realm")
            .get(id=routine.id)
        )
        if routine.executor != ROUTINE_EXECUTOR_AGENT:
            raise JsonableError(_("This routine runs outside Sanji."))
        latest = routine.runs.filter(trigger="manual").order_by("-started_at").first()
        if (
            latest is not None
            and latest.started_at is not None
            and now - latest.started_at < MANUAL_RUN_INTERVAL
        ):
            raise JsonableError(_("This routine ran a moment ago. Wait a minute to run it again."))
        run = AgentRoutineRun.objects.create(
            realm=routine.realm,
            routine=routine,
            trigger="manual",
            scheduled_for=now,
            started_at=now,
            status="running",
        )
        execute_routine_run(routine, run, idempotency_key=uuid4(), now=now)
        send_routine_event(routine, "update")
    return run


def reconcile_routine_runs(realm: Realm, now: datetime | None = None) -> int:
    """Close the runs whose job has ended, with the job's result message and
    status. Returns how many closed. The cost stays unknown, because the
    agent platform does not measure it yet."""
    now = now or timezone_now()
    run_ids = list(
        AgentRoutineRun.objects.filter(routine__realm=realm, status="running")
        .order_by("started_at")
        .values_list("id", flat=True)[:200]
    )
    closed = 0
    for run_id in run_ids:
        with transaction.atomic(savepoint=False):
            run = (
                AgentRoutineRun.objects.select_for_update(
                    skip_locked=True, of=("self",), no_key=True
                )
                .select_related("routine__owner", "routine__realm", "job")
                .filter(id=run_id, status="running")
                .first()
            )
            if run is None:
                continue
            job = run.job
            if job is None:
                _finish_run(run.routine, run, "failed", "job_missing", now)
            elif job.status in TERMINAL:
                run.result_message_id = job.result_message_id
                if job.status == "completed":
                    _finish_run(run.routine, run, "succeeded", "", job.completed_at or now)
                else:
                    _finish_run(run.routine, run, "failed", f"job_{job.status}", now)
            else:
                continue
            send_routine_event(run.routine, "update")
            closed += 1
    return closed
