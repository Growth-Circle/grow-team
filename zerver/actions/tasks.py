"""Write-side operations for the task board.

Moving a card never edits the conversation it came from. Every change a
person can read back is written to the card history instead.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from django.conf import settings as django_settings
from django.db import transaction
from django.db.models import Max, Q
from django.utils.timezone import now as timezone_now
from django.utils.translation import gettext as _
from django.utils.translation import override as override_language

from zerver.actions.message_send import internal_send_private_message, internal_send_stream_message
from zerver.lib.exceptions import JsonableError
from zerver.lib.markdown import MessageRenderingResult, markdown_convert
from zerver.lib.mention import MentionBackend, MentionData, silent_mention_syntax_for_user
from zerver.lib.tasks import (
    realm_task_id_prefix,
    task_api_dict,
    task_event_audience,
    task_note_counts,
    user_can_access_task,
)
from zerver.models import (
    Realm,
    RealmAuditLog,
    Stream,
    Task,
    TaskBoard,
    TaskBoardColumn,
    TaskHistory,
    TaskNote,
    TaskNotification,
    UserProfile,
    agents,
)
from zerver.models.agents import AgentRealmSettings
from zerver.models.realm_audit_logs import AuditLogEventType
from zerver.models.tasks import TASK_PRIORITY_NONE, TASK_SOURCE_MANUAL
from zerver.models.users import active_user_ids, get_system_bot
from zerver.tornado.django_api import send_event_on_commit

# Gap between two cards appended to a column. Wide enough that many moves
# between the same pair of cards never exhaust float precision.
POSITION_STEP = 1000.0


def next_counter(realm: Realm) -> int:
    highest = Task.objects.filter(realm=realm).aggregate(Max("counter"))["counter__max"]
    return 1 if highest is None else highest + 1


def position_at_end(column: TaskBoardColumn) -> float:
    highest = Task.objects.filter(column=column).aggregate(Max("position"))["position__max"]
    return POSITION_STEP if highest is None else highest + POSITION_STEP


def access_column(board: TaskBoard, column_id: int) -> TaskBoardColumn:
    try:
        return TaskBoardColumn.objects.get(id=column_id, board=board)
    except TaskBoardColumn.DoesNotExist:
        raise JsonableError(_("Task board column does not exist."))


def active_board_audience(realm: Realm) -> list[int]:
    return active_user_ids(realm.id)


def send_task_event(
    realm: Realm, task: Task, op: str, *, task_id_prefix: str | None = None
) -> None:
    event: dict[str, Any] = {"type": "task", "op": op}
    if op == "remove":
        event["task_id"] = task.id
        event["board_id"] = task.board_id
    else:
        prefix = task_id_prefix if task_id_prefix is not None else realm_task_id_prefix(realm)
        event["task"] = task_api_dict(
            task, task_id_prefix=prefix, note_count=task_note_counts([task.id]).get(task.id, 0)
        )
    send_event_on_commit(realm, event, task_event_audience(realm, task))


def notify_task_status_change(
    task: Task, previous_column: TaskBoardColumn, column: TaskBoardColumn, actor: UserProfile
) -> None:
    """05-R4: a short system message to the card's topic when its column
    changes, only for a card linked to a room and only when the
    workspace turned status notices on (`task_status_notices`, off by
    default, T-18)."""
    if task.stream_id is None or not task.topic or previous_column.id == column.id:
        return
    realm_settings = AgentRealmSettings.objects.filter(realm=task.realm).first()
    if realm_settings is None or not realm_settings.task_status_notices:
        return
    stream = Stream.objects.filter(id=task.stream_id, realm=task.realm).first()
    if stream is None:
        return
    sender = get_system_bot(django_settings.NOTIFICATION_BOT, task.realm_id)
    # The workspace language, not the mover's: an agent job can move a card.
    with override_language(stream.realm.default_language):
        content = _("{user} moved {task} to {column}.").format(
            user=silent_mention_syntax_for_user(actor),
            task=code_span(task.title),
            column=code_span(column.name),
        )
    internal_send_stream_message(sender, stream, task.topic, content, acting_user=actor)


def code_span(text: str) -> str:
    """Show user text as a Markdown code span, so it cannot mention anyone."""
    return "`" + text.replace("`", "'") + "`"


def agent_profile_for(user: UserProfile | None) -> agents.AgentProfile | None:
    """The agent profile behind a bot user, or None for any other user."""
    if user is None or not user.is_bot:
        return None
    return agents.AgentProfile.objects.filter(bot_user=user).first()


def link_tasks_to_job(job: agents.AgentJob) -> None:
    """05-D8: link the cards made from the job's source message to the job.

    sync_agent_task (05-R1) then moves those cards as the job runs. A card
    that a person or another agent owns keeps its own status.
    """
    if job.source_message_id is None:
        return
    tasks = Task.objects.filter(
        realm_id=job.realm_id, origin_message_id=job.source_message_id
    ).filter(Q(assignee__isnull=True) | Q(assignee_id=job.profile.bot_user_id))
    for task in tasks:
        task.agent_job = job
        task.agent_profile_id = job.profile_id
        task.save(update_fields=["agent_job", "agent_profile"])
        send_task_event(task.realm, task, "update")


def record_history(
    task: Task, acting_user: UserProfile | None, kind: str, extra_data: dict[str, Any]
) -> TaskHistory:
    return TaskHistory.objects.create(
        task=task, acting_user=acting_user, kind=kind, extra_data=extra_data
    )


@transaction.atomic(durable=True)
def do_create_task(
    *,
    user_profile: UserProfile,
    board: TaskBoard,
    column: TaskBoardColumn,
    title: str,
    body: str = "",
    stream_id: int | None = None,
    topic: str = "",
    origin_message_id: int | None = None,
    assignee: UserProfile | None = None,
    reviewer: UserProfile | None = None,
    labels: list[str] | None = None,
    checklist: list[dict[str, Any]] | None = None,
    due_at: datetime | None = None,
    priority: int = TASK_PRIORITY_NONE,
    source: str = TASK_SOURCE_MANUAL,
) -> Task:
    realm = user_profile.realm
    task = Task.objects.create(
        realm=realm,
        board=board,
        column=column,
        counter=next_counter(realm),
        title=title,
        body=body,
        position=position_at_end(column),
        stream_id=stream_id,
        topic=topic,
        origin_message_id=origin_message_id,
        creator=user_profile,
        assignee=assignee,
        agent_profile=agent_profile_for(assignee),
        reviewer=reviewer,
        labels=labels or [],
        checklist=checklist or [],
        due_at=due_at,
        priority=priority,
        source=source,
    )

    record_history(
        task,
        user_profile,
        TaskHistory.CREATED,
        {"column_name": column.name, "stream_id": stream_id, "topic": topic},
    )
    RealmAuditLog.objects.create(
        realm=realm,
        acting_user=user_profile,
        event_type=AuditLogEventType.TASK_CREATED,
        event_time=timezone_now(),
        extra_data={"task_id": task.id, "board_id": board.id},
    )

    send_task_event(realm, task, "add")
    notify_new_responsible(task=task, actor=user_profile)
    return task


def _done_column(columns: list[TaskBoardColumn]) -> TaskBoardColumn:
    """The board's done column. Every board's default columns mark one, so
    the caller only calls this with a non-empty column list."""
    return next(column for column in columns if column.done_window_days is not None)


def _synced_column(status: str, columns: list[TaskBoardColumn]) -> TaskBoardColumn | None:
    """The caller only ever passes the columns of a board that owns the
    task being synced, and that task's own column keeps the list non-empty
    (`TaskBoardColumn.column` is `on_delete=PROTECT`)."""
    if status == "running":
        return columns[1] if len(columns) > 1 else None
    if status == "waiting_for_approval":
        return next((column for column in columns if column.is_review), None)
    return _done_column(columns)  # "completed"; the caller checks the result is published.


def sync_agent_task(job: agents.AgentJob) -> None:
    """Move every task card linked to this job to match its status.

    `running` moves the card to the board's second column, `waiting_for_approval`
    to its review column, and `completed` (once the result is published) to its
    done column. Any other status leaves the card where a person put it.
    """
    if job.status not in {"running", "waiting_for_approval", "completed"}:
        return
    if job.status == "completed" and job.result_message_id is None:
        return
    bot = job.profile.bot_user
    task_id_prefix = realm_task_id_prefix(job.realm)
    for task in Task.objects.filter(agent_job=job).select_related("board", "column"):
        columns = list(TaskBoardColumn.objects.filter(board_id=task.board_id).order_by("order"))
        column = _synced_column(job.status, columns)
        if column is None or column.id == task.column_id:
            continue
        previous_column = task.column
        task.column = column
        task.position = position_at_end(column)
        task.last_updated = timezone_now()
        done_column = column.done_window_days is not None
        if done_column and task.completed_at is None:
            task.completed_at = task.last_updated
        elif not done_column:
            task.completed_at = None
        task.save(update_fields=["column", "position", "completed_at", "last_updated"])
        record_history(
            task,
            bot,
            TaskHistory.MOVED,
            {"from_column_name": previous_column.name, "to_column_name": column.name},
        )
        RealmAuditLog.objects.create(
            realm=task.realm,
            acting_user=bot,
            event_type=AuditLogEventType.TASK_MOVED,
            event_time=task.last_updated,
            extra_data={
                "task_id": task.id,
                "from_column_id": previous_column.id,
                "to_column_id": column.id,
            },
        )
        notify_task_status_change(task, previous_column, column, bot)
        send_task_event(task.realm, task, "update", task_id_prefix=task_id_prefix)


def check_agent_task_move(task: Task, column: TaskBoardColumn) -> None:
    """Raise before any write when this move would land an agent's task in
    a done column before its job is approved. Every write path that can
    move a task into a done column calls this first."""
    if (
        column.done_window_days is not None
        and task.column_id != column.id
        and task.agent_job is not None
        and task.agent_job.status != "completed"
    ):
        raise JsonableError(_("Agent tasks move to done after approval."))


@transaction.atomic(durable=True)
def do_move_task(
    *,
    user_profile: UserProfile,
    task: Task,
    column: TaskBoardColumn,
    position: float | None,
) -> Task:
    check_agent_task_move(task, column)

    previous_column = task.column
    task.column = column
    task.position = position_at_end(column) if position is None else position
    task.last_updated = timezone_now()

    done_column = column.done_window_days is not None
    if done_column and task.completed_at is None:
        task.completed_at = task.last_updated
    elif not done_column:
        task.completed_at = None

    task.save(update_fields=["column", "position", "completed_at", "last_updated"])

    if previous_column.id != column.id:
        record_history(
            task,
            user_profile,
            TaskHistory.MOVED,
            {"from_column_name": previous_column.name, "to_column_name": column.name},
        )
        RealmAuditLog.objects.create(
            realm=task.realm,
            acting_user=user_profile,
            event_type=AuditLogEventType.TASK_MOVED,
            event_time=task.last_updated,
            extra_data={
                "task_id": task.id,
                "from_column_id": previous_column.id,
                "to_column_id": column.id,
            },
        )
        notify_task_status_change(task, previous_column, column, user_profile)

    send_task_event(task.realm, task, "update")
    return task


@transaction.atomic(durable=True)
def do_update_task(
    *,
    user_profile: UserProfile,
    task: Task,
    changes: dict[str, Any],
) -> Task:
    if not changes:
        return task

    assignee_changed = "assignee" in changes and changes["assignee"] != task.assignee
    if assignee_changed:
        changes = {**changes, "agent_profile": agent_profile_for(changes["assignee"])}
    for field, value in changes.items():
        setattr(task, field, value)
    task.last_updated = timezone_now()
    task.save(update_fields=[*changes.keys(), "last_updated"])

    if assignee_changed:
        record_history(
            task,
            user_profile,
            TaskHistory.ASSIGNED,
            {"assignee_id": task.assignee_id},
        )
        notify_new_responsible(task=task, actor=user_profile)
    else:
        record_history(task, user_profile, TaskHistory.EDITED, {"fields": sorted(changes.keys())})

    send_task_event(task.realm, task, "update")
    return task


@transaction.atomic(durable=True)
def do_delete_task(*, user_profile: UserProfile, task: Task) -> None:
    realm = task.realm
    audience = task_event_audience(realm, task)
    event = {"type": "task", "op": "remove", "task_id": task.id, "board_id": task.board_id}

    RealmAuditLog.objects.create(
        realm=realm,
        acting_user=user_profile,
        event_type=AuditLogEventType.TASK_DELETED,
        event_time=timezone_now(),
        extra_data={"task_id": task.id, "board_id": task.board_id},
    )
    task.delete()
    send_event_on_commit(realm, event, audience)


def render_task_note(*, content: str, realm: Realm, author: UserProfile) -> MessageRenderingResult:
    return markdown_convert(
        content,
        message_realm=realm,
        mention_data=MentionData(MentionBackend(realm.id), content, author),
        no_previews=True,
        acting_user=author,
    )


def task_card_link(task: Task) -> str:
    # The board opens from `#tasks`; this is the same `#tasks/<task id>`
    # form that the agent job card links to.
    return f"{task.realm.url}/#tasks/{task.id}"


def notify_task_member(
    *,
    recipient: UserProfile,
    task: Task,
    actor: UserProfile,
    kind: str,
    note: TaskNote | None = None,
) -> None:
    """Open a Needs-you item for `recipient` and send them a direct message
    from the Notification Bot, so a phone push or an email reaches them
    when they are away from the board."""
    TaskNotification.objects.create(user=recipient, task=task, note=note, actor=actor, kind=kind)

    sender = get_system_bot(django_settings.NOTIFICATION_BOT, task.realm_id)
    card = code_span(f"{realm_task_id_prefix(task.realm)}-{task.counter} {task.title}")
    with override_language(recipient.default_language):
        if kind == TaskNotification.MENTION:
            intro = _("{user} mentioned you in a note on {card}.")
        else:
            intro = _("{user} made you responsible for {card}.")
        link_text = _("Open the card")
    content = intro.format(user=silent_mention_syntax_for_user(actor), card=card)
    internal_send_private_message(
        sender, recipient, f"{content}\n\n[{link_text}]({task_card_link(task)})"
    )

    event = {"type": "task_notification", "op": "add", "task_id": task.id, "kind": kind}
    send_event_on_commit(task.realm, event, [recipient.id])


def notify_note_mentions(
    *, note: TaskNote, mentioned_user_ids: set[int], author: UserProfile
) -> list[int]:
    """Notify each member that the note mentions once. Returns the ids of
    mentioned members who were skipped because they cannot read the card, so
    the author can be told. Bots, the author, and wildcard mentions never
    notify and are not reported."""
    mentioned_users = UserProfile.objects.filter(
        id__in=mentioned_user_ids, realm=author.realm, is_active=True, is_bot=False
    ).exclude(id=author.id)
    ignored_user_ids = []
    for user in mentioned_users.order_by("id"):
        if not user_can_access_task(user, note.task):
            ignored_user_ids.append(user.id)
            continue
        notify_task_member(
            recipient=user, task=note.task, actor=author, kind=TaskNotification.MENTION, note=note
        )
    return ignored_user_ids


def notify_new_responsible(*, task: Task, actor: UserProfile) -> None:
    """Tell the member a card was just made theirs, unless they did it
    themselves, the card was cleared, or they cannot read it."""
    assignee = task.assignee
    if (
        assignee is None
        or assignee.is_bot
        or assignee.id == actor.id
        or not user_can_access_task(assignee, task)
    ):
        return
    notify_task_member(recipient=assignee, task=task, actor=actor, kind=TaskNotification.ASSIGNED)


@transaction.atomic(durable=True)
def do_resolve_task_notifications(*, user_profile: UserProfile, task: Task) -> None:
    resolved_count = TaskNotification.objects.filter(
        user=user_profile, task=task, resolved_at__isnull=True
    ).update(resolved_at=timezone_now())
    if resolved_count:
        event = {"type": "task_notification", "op": "resolve", "task_id": task.id}
        send_event_on_commit(task.realm, event, [user_profile.id])


@dataclass
class AddedTaskNote:
    note: TaskNote
    ignored_mention_user_ids: list[int]


@transaction.atomic(durable=True)
def do_add_task_note(*, user_profile: UserProfile, task: Task, content: str) -> AddedTaskNote:
    rendering_result = render_task_note(content=content, realm=task.realm, author=user_profile)
    note = TaskNote.objects.create(
        task=task,
        author=user_profile,
        content=content,
        rendered_content=rendering_result.rendered_content,
    )
    record_history(task, user_profile, TaskHistory.NOTE_ADDED, {"note_id": note.id})

    event = {"type": "task_note", "op": "add", "task_id": task.id, "note": note.to_api_dict()}
    send_event_on_commit(task.realm, event, task_event_audience(task.realm, task))
    send_task_event(task.realm, task, "update")

    ignored_user_ids = notify_note_mentions(
        note=note,
        mentioned_user_ids=rendering_result.personal_mention_user_ids,
        author=user_profile,
    )
    return AddedTaskNote(note=note, ignored_mention_user_ids=ignored_user_ids)


@transaction.atomic(durable=True)
def do_delete_task_note(*, task: Task, note: TaskNote) -> None:
    note_id = note.id
    note.delete()

    event = {"type": "task_note", "op": "remove", "task_id": task.id, "note_id": note_id}
    send_event_on_commit(task.realm, event, task_event_audience(task.realm, task))
    send_task_event(task.realm, task, "update")


@transaction.atomic(durable=True)
def do_rename_task_board(*, user_profile: UserProfile, board: TaskBoard, name: str) -> TaskBoard:
    if board.name == name:
        return board

    board.name = name
    board.save(update_fields=["name"])

    RealmAuditLog.objects.create(
        realm=board.realm,
        acting_user=user_profile,
        event_type=AuditLogEventType.TASK_BOARD_RENAMED,
        event_time=timezone_now(),
        extra_data={"board_id": board.id, "name": name},
    )

    event = {"type": "task_board", "op": "update", "board": board.to_api_dict()}
    send_event_on_commit(board.realm, event, active_board_audience(board.realm))
    return board
