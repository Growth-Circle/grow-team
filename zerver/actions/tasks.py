"""Write-side operations for the task board.

Moving a card never edits the conversation it came from. Every change a
person can read back is written to the card history instead.
"""

from datetime import datetime
from typing import Any

from django.db import transaction
from django.db.models import Max
from django.utils.timezone import now as timezone_now
from django.utils.translation import gettext as _

from zerver.lib.exceptions import JsonableError
from zerver.lib.tasks import task_event_audience
from zerver.models import (
    Realm,
    RealmAuditLog,
    Task,
    TaskBoard,
    TaskBoardColumn,
    TaskHistory,
    UserProfile,
)
from zerver.models.realm_audit_logs import AuditLogEventType
from zerver.models.users import active_user_ids
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


def send_task_event(realm: Realm, task: Task, op: str) -> None:
    event: dict[str, Any] = {"type": "task", "op": op}
    if op == "remove":
        event["task_id"] = task.id
        event["board_id"] = task.board_id
    else:
        event["task"] = task.to_api_dict()
    send_event_on_commit(realm, event, task_event_audience(realm, task))


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
    labels: list[str] | None = None,
    checklist: list[dict[str, Any]] | None = None,
    due_at: datetime | None = None,
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
        labels=labels or [],
        checklist=checklist or [],
        due_at=due_at,
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
    return task


@transaction.atomic(durable=True)
def do_move_task(
    *,
    user_profile: UserProfile,
    task: Task,
    column: TaskBoardColumn,
    position: float | None,
) -> Task:
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
