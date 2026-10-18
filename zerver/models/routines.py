"""Routines: work that an agent does again on a schedule, and the record of
each run. A routine that runs outside Sanji is kept here too, so the
Scheduled page shows everything that repeats, but Sanji never runs it."""

from typing import Any

from django.db import models
from django.db.models import Q

from zerver.models.agents import AgentJob, AgentProfile, AgentRecord
from zerver.models.messages import Message
from zerver.models.streams import Stream
from zerver.models.users import UserProfile

ROUTINE_PATTERN_KINDS = ["every_minutes", "every_hours", "daily", "weekdays", "weekly", "monthly"]

# Sanji runs it, or something outside Sanji does and Sanji only shows it.
ROUTINE_EXECUTOR_AGENT = "agent"
ROUTINE_EXECUTOR_EXTERNAL = "external"
ROUTINE_EXECUTORS = [ROUTINE_EXECUTOR_AGENT, ROUTINE_EXECUTOR_EXTERNAL]

# Why a routine that is not enabled stopped: a person paused it, three runs
# in a row failed, the budget ran out, or the agent or channel went away.
ROUTINE_PAUSED_REASONS = ["", "user", "failures", "budget", "unavailable"]

ROUTINE_MODEL_PRESETS = ["", "fast", "balanced", "best"]

ROUTINE_RUN_STATUSES = [
    "running",
    "succeeded",
    "failed",
    "skipped_budget",
    "skipped_unavailable",
    "skipped_missed",
]

ROUTINE_RUN_TRIGGERS = ["schedule", "manual"]


def _choices(values: list[str]) -> list[tuple[str, str]]:
    return [(value, value) for value in values]


class AgentRoutine(AgentRecord):
    MAX_TITLE_LENGTH = 100
    MAX_INSTRUCTIONS_LENGTH = 4000
    # A topic name cannot be longer than this in Zulip.
    MAX_TOPIC_LENGTH = 60

    title = models.CharField(max_length=MAX_TITLE_LENGTH)
    instructions = models.TextField(default="", db_default="")
    # Both are null only for a routine that runs outside Sanji.
    agent_profile = models.ForeignKey(
        AgentProfile, on_delete=models.PROTECT, null=True, related_name="+"
    )
    # The name an outside routine shows where an agent's name would be.
    external_agent_name = models.CharField(max_length=100, default="", db_default="")
    owner = models.ForeignKey(
        UserProfile, on_delete=models.CASCADE, db_constraint=False, related_name="+"
    )
    stream = models.ForeignKey(Stream, on_delete=models.CASCADE, null=True, related_name="+")
    topic = models.CharField(max_length=MAX_TOPIC_LENGTH, default="", db_default="")
    pattern_kind = models.CharField(max_length=20, choices=_choices(ROUTINE_PATTERN_KINDS))
    interval = models.PositiveSmallIntegerField(null=True, default=None)
    time_of_day = models.TimeField(null=True, default=None)
    # Monday is 0.
    weekdays = models.JSONField(default=list, db_default=[])
    month_day = models.PositiveSmallIntegerField(null=True, default=None)
    timezone = models.CharField(max_length=40, default="Asia/Jakarta", db_default="Asia/Jakarta")
    model_preset = models.CharField(
        max_length=20, choices=_choices(ROUTINE_MODEL_PRESETS), default="", db_default=""
    )
    enabled = models.BooleanField(default=True, db_default=True)
    paused_reason = models.CharField(
        max_length=20, choices=_choices(ROUTINE_PAUSED_REASONS), default="", db_default=""
    )
    next_run_at = models.DateTimeField(null=True, default=None)
    last_run_at = models.DateTimeField(null=True, default=None)
    last_status = models.CharField(max_length=30, default="", db_default="")
    executor = models.CharField(
        max_length=10,
        choices=_choices(ROUTINE_EXECUTORS),
        default=ROUTINE_EXECUTOR_AGENT,
        db_default=ROUTINE_EXECUTOR_AGENT,
    )
    # How many runs in a row failed. A success, or a resume, starts it over.
    failure_streak = models.PositiveSmallIntegerField(default=0, db_default=0)
    # The month (YYYY-MM) of the last budget notice, so a routine tells its
    # owner once a month at most.
    budget_notice_month = models.CharField(max_length=7, default="", db_default="")

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=Q(pattern_kind__in=ROUTINE_PATTERN_KINDS),
                name="agent_routine_pattern_valid",
            ),
            models.CheckConstraint(
                condition=Q(executor__in=ROUTINE_EXECUTORS), name="agent_routine_executor_valid"
            ),
            models.CheckConstraint(
                condition=Q(paused_reason__in=ROUTINE_PAUSED_REASONS),
                name="agent_routine_paused_reason_valid",
            ),
            models.CheckConstraint(
                condition=Q(model_preset__in=ROUTINE_MODEL_PRESETS),
                name="agent_routine_model_preset_valid",
            ),
            models.CheckConstraint(
                condition=Q(executor=ROUTINE_EXECUTOR_EXTERNAL)
                | Q(agent_profile__isnull=False, stream__isnull=False),
                name="agent_routine_agent_has_target",
            ),
        ]
        indexes = [
            models.Index(fields=["realm", "enabled", "next_run_at"], name="agent_routine_due")
        ]

    @property
    def time_of_day_text(self) -> str | None:
        return None if self.time_of_day is None else self.time_of_day.strftime("%H:%M")

    def to_api_dict(self) -> dict[str, Any]:
        return {
            "id": str(self.id),
            "title": self.title,
            "instructions": self.instructions,
            "agent_profile_id": None
            if self.agent_profile_id is None
            else str(self.agent_profile_id),
            "agent_name": (
                self.agent_profile.name
                if self.agent_profile is not None
                else self.external_agent_name
            ),
            "owner_id": self.owner_id,
            "stream_id": self.stream_id,
            "topic": self.topic,
            "pattern_kind": self.pattern_kind,
            "interval": self.interval,
            "time_of_day": self.time_of_day_text,
            "weekdays": self.weekdays,
            "month_day": self.month_day,
            "timezone": self.timezone,
            "model_preset": self.model_preset,
            "enabled": self.enabled,
            "paused_reason": self.paused_reason,
            "next_run_at": None if self.next_run_at is None else int(self.next_run_at.timestamp()),
            "last_run_at": None if self.last_run_at is None else int(self.last_run_at.timestamp()),
            "last_status": self.last_status,
            "executor": self.executor,
        }


class AgentRoutineRun(AgentRecord):
    routine = models.ForeignKey(AgentRoutine, on_delete=models.CASCADE, related_name="runs")
    trigger = models.CharField(
        max_length=10, choices=_choices(ROUTINE_RUN_TRIGGERS), default="schedule"
    )
    # The occurrence this run is for. A manual run uses the moment it was asked for.
    scheduled_for = models.DateTimeField()
    started_at = models.DateTimeField(null=True, default=None)
    finished_at = models.DateTimeField(null=True, default=None)
    status = models.CharField(max_length=30, choices=_choices(ROUTINE_RUN_STATUSES))
    job = models.ForeignKey(AgentJob, on_delete=models.SET_NULL, null=True, related_name="+")
    result_message = models.ForeignKey(
        Message, on_delete=models.SET_NULL, null=True, db_constraint=False, related_name="+"
    )
    error_code = models.CharField(max_length=40, default="", db_default="")
    # Null means that nobody measured the cost. It is never shown as zero.
    cost_microunits = models.PositiveBigIntegerField(null=True, default=None)
    # When the owner was told about this run, so a rerun of the check does not tell twice.
    notified_at = models.DateTimeField(null=True, default=None)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["routine", "scheduled_for"], name="agent_routine_run_unique"
            ),
            models.CheckConstraint(
                condition=Q(status__in=ROUTINE_RUN_STATUSES), name="agent_routine_run_status_valid"
            ),
            models.CheckConstraint(
                condition=Q(trigger__in=ROUTINE_RUN_TRIGGERS),
                name="agent_routine_run_trigger_valid",
            ),
        ]
        indexes = [
            models.Index(fields=["routine", "-scheduled_for"], name="agent_routine_run_recent")
        ]

    def to_api_dict(self) -> dict[str, Any]:
        return {
            "id": str(self.id),
            "routine_id": str(self.routine_id),
            "trigger": self.trigger,
            "scheduled_for": int(self.scheduled_for.timestamp()),
            "started_at": None if self.started_at is None else int(self.started_at.timestamp()),
            "finished_at": None if self.finished_at is None else int(self.finished_at.timestamp()),
            "status": self.status,
            "error_code": self.error_code,
            "result_message_id": self.result_message_id,
            "cost_microunits": self.cost_microunits,
        }
