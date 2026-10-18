"""What the owner of a grant sees before an Ask first call runs: the tool,
where it lands, and the full content, with people and places by name.

The preview is built from the validated arguments by the same account that
would run the call, so a target that account cannot reach fails here, when
the call is asked for, and not when the owner approves it.
"""

from typing import TypedDict, cast

from django.utils.translation import gettext as _

from zerver.lib.exceptions import JsonableError
from zerver.lib.message import access_message
from zerver.lib.streams import access_stream_by_id
from zerver.lib.tasks import access_task_by_id, realm_task_id_prefix
from zerver.models import Stream, UserProfile
from zerver.models.tasks import TaskBoardColumn


class PreviewTarget(TypedDict):
    # "channel", "direct", "message", "task", "column" or "file".
    type: str
    name: str
    topic: str | None
    message_id: int | None


class McpCallPreview(TypedDict):
    tool: str
    target: PreviewTarget
    content: str
    mentions: list[str]


def _target(
    type: str, name: str, *, topic: str | None = None, message_id: int | None = None
) -> PreviewTarget:
    return {"type": type, "name": name, "topic": topic, "message_id": message_id}


def _names(user: UserProfile, user_ids: list[int]) -> list[str]:
    """The full names of the given active members, in the order given. An id
    that is not an active member of the workspace fails the whole preview."""
    members = {
        member.id: member.full_name
        for member in UserProfile.objects.filter(realm=user.realm, id__in=user_ids, is_active=True)
    }
    if not set(user_ids) <= set(members):
        raise ValueError("A member is not available to this account.")
    return [members[user_id] for user_id in dict.fromkeys(user_ids)]


def _message_target(user: UserProfile, message_id: int, *, modifying: bool) -> PreviewTarget:
    message = access_message(user, message_id, is_modifying_message=modifying)
    if modifying and message.sender_id != user.id:
        raise ValueError("Only the sender can edit a message.")
    if message.is_channel_message:
        stream = Stream.objects.get(id=message.recipient.type_id)
        return _target("message", stream.name, topic=message.topic_name(), message_id=message_id)
    return _target("message", "", message_id=message_id)


def _task_name(user: UserProfile, task_id: int) -> str:
    task = access_task_by_id(user, task_id)
    return f"{realm_task_id_prefix(user.realm)}-{task.counter} {task.title}"


def _column_name(user: UserProfile, column_id: int) -> str:
    column = TaskBoardColumn.objects.filter(id=column_id, board__realm=user.realm).first()
    if column is None:
        raise JsonableError(_("Column does not exist."))
    return column.name


def _task_changes(user: UserProfile, args: dict[str, object]) -> str:
    lines = []
    for field, value in args.items():
        if field == "task_id" or value is None or (field == "clear_due_at" and value is False):
            continue
        if field == "column_id":
            lines.append(f"column: {_column_name(user, cast(int, value))}")
        elif field == "assignee_id":
            lines.append(f"assignee: {_names(user, [cast(int, value)])[0]}")
        elif isinstance(value, list):
            lines.append(f"{field}: {', '.join(str(item) for item in value)}")
        else:
            lines.append(f"{field}: {value}")
    return "\n".join(lines)


def build_preview(user: UserProfile, tool_name: str, args: dict[str, object]) -> McpCallPreview:
    mentions = (
        _names(user, cast(list[int], args["mention_user_ids"]))
        if args.get("mention_user_ids")
        else []
    )
    target: PreviewTarget
    if tool_name == "send_message":
        stream, _subscription = access_stream_by_id(user, cast(int, args["channel_id"]))
        target = _target("channel", stream.name, topic=cast(str, args["topic"]))
        content = cast(str, args["content"])
    elif tool_name == "send_direct_message":
        target = _target(
            "direct", ", ".join(_names(user, cast(list[int], args["recipient_user_ids"])))
        )
        content = cast(str, args["content"])
    elif tool_name == "edit_message":
        target = _message_target(user, cast(int, args["message_id"]), modifying=True)
        content = cast(str, args["content"])
    elif tool_name in {"add_reaction", "remove_reaction"}:
        target = _message_target(user, cast(int, args["message_id"]), modifying=False)
        content = f":{args['emoji_name']}:"
    elif tool_name == "delete_task":
        target = _target("task", _task_name(user, cast(int, args["task_id"])))
        content = ""
    elif tool_name == "update_task":
        target = _target("task", _task_name(user, cast(int, args["task_id"])))
        content = _task_changes(user, args)
    elif tool_name == "create_task":
        target = _target("column", _column_name(user, cast(int, args["column_id"])))
        content = "\n".join(
            part for part in (cast(str, args["title"]), cast(str, args.get("body", ""))) if part
        )
    elif tool_name == "upload_file":
        target = _target("file", cast(str, args["filename"]))
        encoded = cast(str, args["content_base64"])
        size = len(encoded) * 3 // 4 - (len(encoded) - len(encoded.rstrip("=")))
        content = f"{args['content_type']}, {size} bytes"
    else:
        raise ValueError("Unknown tool.")
    return {"tool": tool_name, "target": target, "content": content, "mentions": mentions}
