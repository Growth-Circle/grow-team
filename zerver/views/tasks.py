from datetime import datetime, timezone
from typing import Annotated, Any

from django.http import HttpRequest, HttpResponse
from django.utils.translation import gettext as _
from pydantic import Json, StringConstraints

from zerver.actions.tasks import (
    access_column,
    do_create_task,
    do_delete_task,
    do_move_task,
    do_rename_task_board,
    do_update_task,
)
from zerver.lib.exceptions import JsonableError
from zerver.lib.message import access_message
from zerver.lib.response import json_success
from zerver.lib.streams import access_stream_by_id
from zerver.lib.tasks import (
    access_board_by_id,
    access_task_by_id,
    get_or_create_default_board,
    hidden_done_task_ids,
    visible_tasks,
)
from zerver.lib.typed_endpoint import PathOnly, typed_endpoint
from zerver.lib.users import access_user_by_id
from zerver.models import Task, TaskBoard, TaskHistory, UserProfile

TaskTitle = Annotated[
    str, StringConstraints(min_length=1, max_length=Task.MAX_TITLE_LENGTH, strip_whitespace=True)
]
TaskBody = Annotated[str, StringConstraints(max_length=Task.MAX_BODY_LENGTH)]
BoardName = Annotated[
    str,
    StringConstraints(min_length=1, max_length=TaskBoard.MAX_NAME_LENGTH, strip_whitespace=True),
]


def clean_labels(labels: list[str]) -> list[str]:
    if len(labels) > Task.MAX_LABELS:
        raise JsonableError(_("Too many labels."))
    cleaned = []
    for label in labels:
        label = label.strip()
        if not label:
            continue
        if len(label) > Task.MAX_LABEL_LENGTH:
            raise JsonableError(_("Label is too long."))
        cleaned.append(label)
    return cleaned


def clean_checklist(checklist: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if len(checklist) > Task.MAX_CHECKLIST_ITEMS:
        raise JsonableError(_("Too many checklist items."))
    cleaned = []
    for item in checklist:
        text = str(item.get("text", "")).strip()
        if not text:
            continue
        cleaned.append({"text": text[: Task.MAX_TITLE_LENGTH], "done": bool(item.get("done"))})
    return cleaned


def timestamp_to_datetime(value: int) -> datetime:
    return datetime.fromtimestamp(value, tz=timezone.utc)


@typed_endpoint
def get_task_board(
    request: HttpRequest,
    user_profile: UserProfile,
    *,
    board_id: Json[int] | None = None,
) -> HttpResponse:
    if board_id is None:
        board = get_or_create_default_board(user_profile.realm)
    else:
        board = access_board_by_id(user_profile.realm, board_id)

    columns = list(board.columns.all())
    tasks = visible_tasks(user_profile, board)
    hidden_ids = hidden_done_task_ids(columns, tasks)

    return json_success(
        request,
        data={
            "board": board.to_api_dict(),
            "columns": [column.to_api_dict() for column in columns],
            "tasks": [task.to_api_dict() for task in tasks],
            "folded_task_ids": sorted(hidden_ids),
        },
    )


@typed_endpoint
def create_task(
    request: HttpRequest,
    user_profile: UserProfile,
    *,
    assignee_id: Json[int] | None = None,
    body: TaskBody = "",
    checklist: Json[list[dict[str, Any]]] | None = None,
    column_id: Json[int],
    due_at: Json[int] | None = None,
    labels: Json[list[str]] | None = None,
    origin_message_id: Json[int] | None = None,
    stream_id: Json[int] | None = None,
    title: TaskTitle,
    topic: str = "",
) -> HttpResponse:
    board = get_or_create_default_board(user_profile.realm)
    column = access_column(board, column_id)

    if stream_id is not None:
        # Raises if the user cannot read the channel the card claims to
        # come from, so nobody can seed the board with a card they would
        # not be allowed to see.
        access_stream_by_id(user_profile, stream_id, require_content_access=True)

    if origin_message_id is not None:
        access_message(user_profile, origin_message_id, is_modifying_message=False)

    assignee = None
    if assignee_id is not None:
        assignee = access_user_by_id(
            user_profile, assignee_id, allow_bots=True, for_admin=False
        )

    task = do_create_task(
        user_profile=user_profile,
        board=board,
        column=column,
        title=title,
        body=body,
        stream_id=stream_id,
        topic=topic,
        origin_message_id=origin_message_id,
        assignee=assignee,
        labels=clean_labels(labels or []),
        checklist=clean_checklist(checklist or []),
        due_at=None if due_at is None else timestamp_to_datetime(due_at),
    )
    return json_success(request, data={"task_id": task.id, "display_id": task.display_id})


@typed_endpoint
def update_task(
    request: HttpRequest,
    user_profile: UserProfile,
    *,
    assignee_id: Json[int] | None = None,
    blocked: Json[bool] | None = None,
    body: TaskBody | None = None,
    checklist: Json[list[dict[str, Any]]] | None = None,
    clear_assignee: Json[bool] = False,
    clear_due_at: Json[bool] = False,
    column_id: Json[int] | None = None,
    due_at: Json[int] | None = None,
    labels: Json[list[str]] | None = None,
    position: Json[float] | None = None,
    task_id: PathOnly[int],
    title: TaskTitle | None = None,
) -> HttpResponse:
    task = access_task_by_id(user_profile, task_id)

    changes: dict[str, Any] = {}
    if title is not None:
        changes["title"] = title
    if body is not None:
        changes["body"] = body
    if blocked is not None:
        changes["blocked"] = blocked
    if labels is not None:
        changes["labels"] = clean_labels(labels)
    if checklist is not None:
        changes["checklist"] = clean_checklist(checklist)
    if clear_due_at:
        changes["due_at"] = None
    elif due_at is not None:
        changes["due_at"] = timestamp_to_datetime(due_at)
    if clear_assignee:
        changes["assignee"] = None
    elif assignee_id is not None:
        changes["assignee"] = access_user_by_id(
            user_profile, assignee_id, allow_bots=True, for_admin=False
        )

    if changes:
        do_update_task(user_profile=user_profile, task=task, changes=changes)

    if column_id is not None:
        column = access_column(task.board, column_id)
        do_move_task(user_profile=user_profile, task=task, column=column, position=position)
    elif position is not None:
        do_move_task(user_profile=user_profile, task=task, column=task.column, position=position)

    return json_success(request)


def delete_task(
    request: HttpRequest,
    user_profile: UserProfile,
    *,
    task_id: int,
) -> HttpResponse:
    task = access_task_by_id(user_profile, task_id)
    do_delete_task(user_profile=user_profile, task=task)
    return json_success(request)


def get_task_history(
    request: HttpRequest,
    user_profile: UserProfile,
    *,
    task_id: int,
) -> HttpResponse:
    task = access_task_by_id(user_profile, task_id)
    history = TaskHistory.objects.filter(task=task)
    return json_success(request, data={"history": [entry.to_api_dict() for entry in history]})


@typed_endpoint
def update_task_board(
    request: HttpRequest,
    user_profile: UserProfile,
    *,
    board_id: PathOnly[int],
    name: BoardName,
) -> HttpResponse:
    board = access_board_by_id(user_profile.realm, board_id)
    do_rename_task_board(user_profile=user_profile, board=board, name=name)
    return json_success(request)
