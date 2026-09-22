"""Read-side helpers for the task board.

A card inherits the access of the conversation it came from. A card whose
origin channel the user cannot read never reaches that user, neither in a
fetch nor in an event.
"""

from datetime import timedelta
from typing import Any

from django.db.models import QuerySet
from django.utils.timezone import now as timezone_now
from django.utils.translation import gettext as _

from zerver.lib.exceptions import JsonableError
from zerver.lib.stream_subscription import get_active_subscriptions_for_stream_id
from zerver.lib.streams import get_content_access_streams
from zerver.lib.user_groups import UserGroupMembershipDetails
from zerver.models import Realm, Stream, Task, TaskBoard, TaskBoardColumn, UserProfile
from zerver.models.users import active_non_guest_user_ids, active_user_ids

# The default board every realm starts with. The guide leaves the final
# column names to each team, so these are only the starting point.
DEFAULT_BOARD_NAME = "Task board"
DEFAULT_COLUMNS: list[dict[str, Any]] = [
    {"name": "Inbox", "work_limit": None, "done_window_days": None},
    {"name": "In progress", "work_limit": 3, "done_window_days": None},
    {"name": "Awaiting review", "work_limit": None, "done_window_days": None},
    {"name": "Done", "work_limit": None, "done_window_days": 7},
]


def get_or_create_default_board(realm: Realm) -> TaskBoard:
    board = TaskBoard.objects.filter(realm=realm).order_by("id").first()
    if board is not None:
        return board

    board = TaskBoard.objects.create(realm=realm, name=DEFAULT_BOARD_NAME)
    TaskBoardColumn.objects.bulk_create(
        TaskBoardColumn(
            board=board,
            name=column["name"],
            order=order,
            work_limit=column["work_limit"],
            done_window_days=column["done_window_days"],
        )
        for order, column in enumerate(DEFAULT_COLUMNS)
    )
    return board


def access_board_by_id(realm: Realm, board_id: int) -> TaskBoard:
    try:
        return TaskBoard.objects.get(id=board_id, realm=realm)
    except TaskBoard.DoesNotExist:
        raise JsonableError(_("Task board does not exist."))


def readable_stream_ids(user_profile: UserProfile, stream_ids: set[int]) -> set[int]:
    """Return the subset of stream_ids whose content this user may read."""
    if not stream_ids:
        return set()

    streams = list(Stream.objects.filter(id__in=stream_ids, realm=user_profile.realm))
    readable = get_content_access_streams(
        user_profile, streams, UserGroupMembershipDetails(user_recursive_group_ids=None)
    )
    return {stream.id for stream in readable}


def visible_tasks(user_profile: UserProfile, board: TaskBoard) -> list[Task]:
    """Cards on this board that the user may read, newest activity first."""
    tasks: QuerySet[Task] = Task.objects.filter(board=board, realm=user_profile.realm)
    candidates = list(tasks.order_by("column__order", "position", "id"))

    origin_stream_ids = {task.stream_id for task in candidates if task.stream_id is not None}
    allowed = readable_stream_ids(user_profile, origin_stream_ids)
    return [task for task in candidates if task.stream_id is None or task.stream_id in allowed]


def hidden_done_task_ids(columns: list[TaskBoardColumn], tasks: list[Task]) -> set[int]:
    """Cards a column's done window folds away behind one row."""
    windows = {
        column.id: column.done_window_days
        for column in columns
        if column.done_window_days is not None
    }
    if not windows:
        return set()

    now = timezone_now()
    hidden = set()
    for task in tasks:
        window_days = windows.get(task.column_id)
        if window_days is None:
            continue
        finished_at = task.completed_at or task.last_updated
        if finished_at < now - timedelta(days=window_days):
            hidden.add(task.id)
    return hidden


def user_can_access_task(user_profile: UserProfile, task: Task) -> bool:
    if task.realm_id != user_profile.realm_id:
        return False
    if task.stream_id is None:
        return True
    return task.stream_id in readable_stream_ids(user_profile, {task.stream_id})


def access_task_by_id(user_profile: UserProfile, task_id: int) -> Task:
    try:
        task = Task.objects.get(id=task_id, realm=user_profile.realm)
    except Task.DoesNotExist:
        raise JsonableError(_("Task does not exist."))

    if not user_can_access_task(user_profile, task):
        raise JsonableError(_("Task does not exist."))
    return task


def task_event_audience(realm: Realm, task: Task) -> list[int]:
    """User ids allowed to see this card's events.

    A manual card reaches the whole realm. A card from a channel reaches
    the people who can read that channel.
    """
    if task.stream_id is None:
        return active_user_ids(realm.id)

    stream = Stream.objects.filter(id=task.stream_id).first()
    if stream is None:
        return active_user_ids(realm.id)

    subscriber_ids = set(
        get_active_subscriptions_for_stream_id(
            stream.id, include_deactivated_users=False
        ).values_list("user_profile_id", flat=True)
    )
    if stream.invite_only:
        return sorted(subscriber_ids)

    # A public channel is readable by every non-guest member, plus any
    # guest who is actually subscribed to it.
    return sorted(set(active_non_guest_user_ids(realm.id)) | subscriber_ids)
