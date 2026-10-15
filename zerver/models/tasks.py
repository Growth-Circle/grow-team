"""Task board records. A card keeps a reference to the conversation it came
from, so the board and the topic never become two sources of truth."""

from typing import Any

from django.db import models
from django.db.models import Q
from django.utils.timezone import now as timezone_now
from zerver.models.agents import AgentJob, AgentProfile
from zerver.models.messages import Message
from zerver.models.realms import Realm
from zerver.models.streams import Stream
from zerver.models.users import UserProfile

# Cards are addressed by a short human-readable id. The counter is unique
# per realm; the prefix identifies the product.
TASK_ID_PREFIX = "GT"


def format_task_id(counter: int) -> str:
    return f"{TASK_ID_PREFIX}-{counter}"


# Where a card was made from.
TASK_SOURCE_MANUAL = "manual"
TASK_SOURCE_BRIEF = "brief"
TASK_SOURCE_MEETING = "meeting"
TASK_SOURCE_WHATSAPP = "whatsapp"
TASK_SOURCE_AGENT = "agent"

TASK_SOURCES = [
    TASK_SOURCE_MANUAL,
    TASK_SOURCE_BRIEF,
    TASK_SOURCE_MEETING,
    TASK_SOURCE_WHATSAPP,
    TASK_SOURCE_AGENT,
]

TASK_SOURCE_CHOICES = [(source, source) for source in TASK_SOURCES]

# Ordered so a larger number is more urgent; 0 means nobody set one.
TASK_PRIORITY_NONE = 0
TASK_PRIORITY_LOW = 1
TASK_PRIORITY_MEDIUM = 2
TASK_PRIORITY_HIGH = 3

TASK_PRIORITIES = [
    TASK_PRIORITY_NONE,
    TASK_PRIORITY_LOW,
    TASK_PRIORITY_MEDIUM,
    TASK_PRIORITY_HIGH,
]

TASK_PRIORITY_CHOICES = [
    (TASK_PRIORITY_NONE, "none"),
    (TASK_PRIORITY_LOW, "low"),
    (TASK_PRIORITY_MEDIUM, "medium"),
    (TASK_PRIORITY_HIGH, "high"),
]


class TaskBoard(models.Model):
    MAX_NAME_LENGTH = 60

    realm = models.ForeignKey(Realm, on_delete=models.CASCADE)
    name = models.CharField(max_length=MAX_NAME_LENGTH)
    date_created = models.DateTimeField(default=timezone_now)

    class Meta:
        unique_together = ("realm", "name")

    def to_api_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "date_created": int(self.date_created.timestamp()),
        }


class TaskBoardColumn(models.Model):
    MAX_NAME_LENGTH = 60

    board = models.ForeignKey(TaskBoard, on_delete=models.CASCADE, related_name="columns")
    name = models.CharField(max_length=MAX_NAME_LENGTH)
    order = models.PositiveIntegerField()
    # A column with a work limit warns once it holds that many cards. The
    # limit never blocks a move; it only asks the mover to confirm.
    work_limit = models.PositiveIntegerField(null=True, default=None)
    # A column with a done window hides cards finished before it.
    done_window_days = models.PositiveIntegerField(null=True, default=None)
    # Cards in a review column count toward their reviewer's
    # "Awaiting my review" total.
    is_review = models.BooleanField(default=False)

    class Meta:
        ordering = ["order"]
        unique_together = ("board", "order")

    def to_api_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "board_id": self.board_id,
            "name": self.name,
            "order": self.order,
            "work_limit": self.work_limit,
            "done_window_days": self.done_window_days,
            "is_review": self.is_review,
        }


class Task(models.Model):
    MAX_TITLE_LENGTH = 200
    MAX_BODY_LENGTH = 10000
    MAX_LABELS = 10
    MAX_LABEL_LENGTH = 40
    MAX_CHECKLIST_ITEMS = 50

    realm = models.ForeignKey(Realm, on_delete=models.CASCADE)
    board = models.ForeignKey(TaskBoard, on_delete=models.CASCADE, related_name="tasks")
    column = models.ForeignKey(TaskBoardColumn, on_delete=models.PROTECT, related_name="tasks")
    # Rendered as GT-<counter>. Unique per realm, never reused.
    counter = models.PositiveIntegerField()
    title = models.CharField(max_length=MAX_TITLE_LENGTH)
    body = models.TextField(max_length=MAX_BODY_LENGTH, default="")
    # Sort key inside a column. Floats let a move between two cards write
    # one row instead of renumbering the column.
    position = models.FloatField(default=0)

    # Where the card came from. A card without a channel is a manual card
    # and is visible to everyone in the realm.
    stream = models.ForeignKey(Stream, on_delete=models.SET_NULL, null=True)
    topic = models.TextField(default="")
    origin_message = models.ForeignKey(
        Message, on_delete=models.SET_NULL, null=True, related_name="task_cards"
    )

    creator = models.ForeignKey(UserProfile, on_delete=models.CASCADE, related_name="created_tasks")
    assignee = models.ForeignKey(
        UserProfile, on_delete=models.SET_NULL, null=True, related_name="assigned_tasks"
    )
    reviewer = models.ForeignKey(
        UserProfile, on_delete=models.SET_NULL, null=True, related_name="reviewed_tasks"
    )
    agent_profile = models.ForeignKey(AgentProfile, on_delete=models.SET_NULL, null=True)
    agent_job = models.ForeignKey(AgentJob, on_delete=models.SET_NULL, null=True)

    source = models.CharField(
        max_length=20,
        choices=TASK_SOURCE_CHOICES,
        default=TASK_SOURCE_MANUAL,
        db_default=TASK_SOURCE_MANUAL,
    )

    priority = models.PositiveSmallIntegerField(
        choices=TASK_PRIORITY_CHOICES,
        default=TASK_PRIORITY_NONE,
        db_default=TASK_PRIORITY_NONE,
    )
    labels = models.JSONField(default=list)
    checklist = models.JSONField(default=list)
    due_at = models.DateTimeField(null=True, default=None)
    blocked = models.BooleanField(default=False)
    completed_at = models.DateTimeField(null=True, default=None)

    date_created = models.DateTimeField(default=timezone_now)
    last_updated = models.DateTimeField(default=timezone_now)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["realm", "counter"], name="task_counter_unique"),
            models.CheckConstraint(condition=Q(source__in=TASK_SOURCES), name="task_source_valid"),
            models.CheckConstraint(
                condition=Q(priority__in=TASK_PRIORITIES), name="task_priority_valid"
            ),
        ]
        indexes = [models.Index(fields=["board", "column", "position"])]

    @property
    def display_id(self) -> str:
        return format_task_id(self.counter)

    def to_api_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "display_id": self.display_id,
            "board_id": self.board_id,
            "column_id": self.column_id,
            "title": self.title,
            "body": self.body,
            "position": self.position,
            "stream_id": self.stream_id,
            "topic": self.topic,
            "origin_message_id": self.origin_message_id,
            "creator_id": self.creator_id,
            "assignee_id": self.assignee_id,
            "reviewer_id": self.reviewer_id,
            "agent_profile_id": None
            if self.agent_profile_id is None
            else str(self.agent_profile_id),
            "agent_job_id": None if self.agent_job_id is None else str(self.agent_job_id),
            "priority": self.priority,
            "labels": self.labels,
            "checklist": self.checklist,
            "due_at": None if self.due_at is None else int(self.due_at.timestamp()),
            "blocked": self.blocked,
            "completed_at": (
                None if self.completed_at is None else int(self.completed_at.timestamp())
            ),
            "date_created": int(self.date_created.timestamp()),
            "last_updated": int(self.last_updated.timestamp()),
        }


TASK_HISTORY_CREATED = "created"
TASK_HISTORY_MOVED = "moved"
TASK_HISTORY_EDITED = "edited"
TASK_HISTORY_ASSIGNED = "assigned"
TASK_HISTORY_NOTE_ADDED = "note_added"

TASK_HISTORY_KINDS = [
    TASK_HISTORY_CREATED,
    TASK_HISTORY_MOVED,
    TASK_HISTORY_EDITED,
    TASK_HISTORY_ASSIGNED,
    TASK_HISTORY_NOTE_ADDED,
]

TASK_HISTORY_KIND_CHOICES = [(kind, kind) for kind in TASK_HISTORY_KINDS]


class TaskHistory(models.Model):
    """One row per change a person can read back on the card."""

    CREATED = TASK_HISTORY_CREATED
    MOVED = TASK_HISTORY_MOVED
    EDITED = TASK_HISTORY_EDITED
    ASSIGNED = TASK_HISTORY_ASSIGNED
    NOTE_ADDED = TASK_HISTORY_NOTE_ADDED

    task = models.ForeignKey(Task, on_delete=models.CASCADE, related_name="history")
    acting_user = models.ForeignKey(UserProfile, on_delete=models.SET_NULL, null=True)
    kind = models.CharField(max_length=20, choices=TASK_HISTORY_KIND_CHOICES)
    event_time = models.DateTimeField(default=timezone_now)
    extra_data = models.JSONField(default=dict)

    class Meta:
        ordering = ["-event_time", "-id"]
        constraints = [
            models.CheckConstraint(
                condition=Q(kind__in=TASK_HISTORY_KINDS),
                name="task_history_kind_valid",
            )
        ]

    def to_api_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "task_id": self.task_id,
            "acting_user_id": self.acting_user_id,
            "kind": self.kind,
            "event_time": int(self.event_time.timestamp()),
            "extra_data": self.extra_data,
        }


TASK_NOTIFICATION_MENTION = "mention"
TASK_NOTIFICATION_ASSIGNED = "assigned"

TASK_NOTIFICATION_KINDS = [TASK_NOTIFICATION_MENTION, TASK_NOTIFICATION_ASSIGNED]

TASK_NOTIFICATION_KIND_CHOICES = [(kind, kind) for kind in TASK_NOTIFICATION_KINDS]


class TaskNote(models.Model):
    """A comment on a card. Unlike the card body, a note belongs to the person
    who wrote it, so only that person or an admin may remove it."""

    MAX_CONTENT_LENGTH = 10000

    task = models.ForeignKey(Task, on_delete=models.CASCADE, related_name="notes")
    author = models.ForeignKey(UserProfile, on_delete=models.CASCADE, related_name="+")
    content = models.TextField(max_length=MAX_CONTENT_LENGTH)
    rendered_content = models.TextField()
    date_created = models.DateTimeField(default=timezone_now)

    class Meta:
        ordering = ["date_created", "id"]

    def to_api_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "task_id": self.task_id,
            "author_id": self.author_id,
            "content": self.content,
            "rendered_content": self.rendered_content,
            "date_created": int(self.date_created.timestamp()),
        }


class TaskNotification(models.Model):
    """Something on a card that a member should look at: a note that
    mentions them, or the card being handed to them. It stays open, and
    shows in their Needs-you list, until they open the card."""

    MENTION = TASK_NOTIFICATION_MENTION
    ASSIGNED = TASK_NOTIFICATION_ASSIGNED

    user = models.ForeignKey(UserProfile, on_delete=models.CASCADE, related_name="+")
    task = models.ForeignKey(Task, on_delete=models.CASCADE, related_name="notifications")
    # Set for a mention, so the Needs-you item can quote the note.
    note = models.ForeignKey(
        TaskNote, on_delete=models.CASCADE, null=True, related_name="notifications"
    )
    actor = models.ForeignKey(UserProfile, on_delete=models.CASCADE, related_name="+")
    kind = models.CharField(max_length=20, choices=TASK_NOTIFICATION_KIND_CHOICES)
    date_created = models.DateTimeField(default=timezone_now)
    resolved_at = models.DateTimeField(null=True, default=None)

    class Meta:
        ordering = ["-date_created", "-id"]
        constraints = [
            models.CheckConstraint(
                condition=Q(kind__in=TASK_NOTIFICATION_KINDS),
                name="task_notification_kind_valid",
            )
        ]


class TaskReminderLog(models.Model):
    """The morning reminder a member got on one day. The row is the
    marker: a member with a row for the workspace's date gets no second
    reminder that day, however often the job runs."""

    user = models.ForeignKey(UserProfile, on_delete=models.CASCADE, related_name="+")
    # The date on the workspace's clock, not UTC.
    date = models.DateField()

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["user", "date"], name="task_reminder_log_unique"),
        ]
