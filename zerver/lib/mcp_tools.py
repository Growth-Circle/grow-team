"""MCP tools use the workspace's existing permission checks."""

import base64
import binascii
import json
import re
from collections.abc import Collection
from copy import copy
from typing import TYPE_CHECKING, Annotated, Any, Literal, cast

from django.core.files.uploadedfile import SimpleUploadedFile
from django.http import HttpRequest, QueryDict
from django.utils.datastructures import MultiValueDict
from django.views.decorators.debug import sensitive_variables
from pydantic import AfterValidator, BaseModel, ConfigDict, Field, StringConstraints

from zerver.lib.exceptions import JsonableError
from zerver.lib.message import access_message
from zerver.lib.request import RequestNotes
from zerver.lib.tasks import access_task_by_id
from zerver.models import UserProfile
from zerver.views import needs, reactions, streams, tasks, upload, users
from zerver.views.message_edit import update_message_backend
from zerver.views.message_fetch import get_messages_backend
from zerver.views.message_send import send_message_backend

if TYPE_CHECKING:
    from django.http.request import _ImmutableQueryDict

PositiveId = Annotated[int, Field(strict=True, gt=0)]
ShortText = Annotated[str, StringConstraints(min_length=1, max_length=200)]
# The largest file upload_file accepts. The realm's own upload limit applies
# below it. The base64 form, with a data URL prefix and line breaks, must stay
# inside both MAX_BODY_BYTES of the MCP view and DATA_UPLOAD_MAX_MEMORY_SIZE.
MAX_FILE_MEBIBYTES = 15
MAX_BASE64_LENGTH = 21_000_000
DATA_URL_PREFIX = re.compile(r"^data:[^,;]*(;[^,;]*)*;base64,", re.IGNORECASE)


class ToolInputError(ValueError):
    """A rejection whose message is safe to show to the client."""

    reason = "tool_input"


class ReadOnlyConnectionError(ToolInputError):
    reason = "read_only"


class NotAccessibleError(ToolInputError):
    """The item is missing or the user may not read it; the two are not told apart."""

    reason = "not_accessible"


class Arguments(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Search(Arguments):
    query: Annotated[str, StringConstraints(min_length=1, max_length=500)]


class Fetch(Arguments):
    id: Annotated[str, StringConstraints(pattern=r"^(message|task):[1-9][0-9]{0,17}$")]


class Channel(Arguments):
    channel_id: PositiveId


class Messages(Channel):
    topic: Annotated[str, StringConstraints(max_length=60)] | None = None
    limit: Annotated[int, Field(ge=1, le=100)] = 30
    before: PositiveId | None = None


class Send(Channel):
    topic: Annotated[str, StringConstraints(max_length=60)]
    content: Annotated[str, StringConstraints(min_length=1, max_length=10000)]
    mention_user_ids: Annotated[list[PositiveId], Field(max_length=20)] = Field(
        default_factory=list
    )


class DirectMessage(Arguments):
    recipient_user_ids: Annotated[list[PositiveId], Field(min_length=1, max_length=10)]
    content: Annotated[str, StringConstraints(min_length=1, max_length=10000)]
    mention_user_ids: Annotated[list[PositiveId], Field(max_length=20)] = Field(
        default_factory=list
    )


class DirectMessages(Arguments):
    user_ids: Annotated[list[PositiveId], Field(min_length=1, max_length=10)]
    limit: Annotated[int, Field(ge=1, le=100)] = 30
    before: PositiveId | None = None


class EditMessage(Arguments):
    message_id: PositiveId
    content: Annotated[str, StringConstraints(min_length=1, max_length=10000)]


class Reaction(Arguments):
    message_id: PositiveId
    emoji_name: Annotated[str, StringConstraints(min_length=1, max_length=100)]


class UploadFile(Arguments):
    filename: Annotated[str, StringConstraints(min_length=1, max_length=100)]
    content_base64: Annotated[str, StringConstraints(min_length=1, max_length=MAX_BASE64_LENGTH)]
    content_type: Annotated[
        str, StringConstraints(max_length=100, pattern=r"^[\w.+-]+/[\w.+-]+$")
    ] = "application/octet-stream"


class GetNeeds(Arguments):
    kind: (
        Literal["approval", "mention", "decision", "card_mention", "card_assigned", "reply"] | None
    ) = None
    # "resolved" lists the items that the account handled today.
    status: Literal["open", "resolved"] = "open"
    include_team_wide: bool = False
    limit: Annotated[int, Field(ge=1, le=100)] = 30


# Approvals, held calls and agent questions stay visible to the person until
# they answer them in the app, so no tool hides or closes them.
MCP_HANDLED_NEED_KINDS = frozenset({"mention", "card_mention", "card_assigned", "reply"})


def check_need_kind(need_id: str) -> str:
    # Checked with the arguments, so an Ask first call for another kind is
    # never held for the owner to approve.
    if need_id.partition(":")[0] not in MCP_HANDLED_NEED_KINDS:
        raise ValueError(
            "Only mentions, card items and replies can be handled here. Answer approvals and agent questions in the app."
        )
    return need_id


NeedId = Annotated[
    str,
    StringConstraints(min_length=3, max_length=80, pattern=r"^[a-z_]+:\S+$"),
    AfterValidator(check_need_kind),
]


class NeedIds(Arguments):
    need_ids: Annotated[list[NeedId], Field(min_length=1, max_length=200)]


class TaskId(Arguments):
    task_id: PositiveId


class Members(Arguments):
    query: Annotated[str, StringConstraints(max_length=100)] = ""
    limit: Annotated[int, Field(ge=1, le=100)] = 30


class CreateTask(Arguments):
    title: Annotated[str, StringConstraints(min_length=1, max_length=200)]
    column_id: PositiveId
    body: Annotated[str, StringConstraints(max_length=10000)] = ""
    stream_id: PositiveId | None = None
    topic: Annotated[str, StringConstraints(max_length=60)] = ""
    assignee_id: PositiveId | None = None
    priority: Annotated[int, Field(ge=0, le=3)] = 0
    labels: Annotated[
        list[Annotated[str, StringConstraints(max_length=40)]], Field(max_length=10)
    ] = Field(default_factory=list)
    due_at: PositiveId | None = None


class UpdateTask(Arguments):
    task_id: PositiveId
    title: Annotated[str, StringConstraints(min_length=1, max_length=200)] | None = None
    column_id: PositiveId | None = None
    body: Annotated[str, StringConstraints(max_length=10000)] | None = None
    assignee_id: PositiveId | None = None
    blocked: bool | None = None
    priority: Annotated[int, Field(ge=0, le=3)] | None = None
    labels: (
        Annotated[list[Annotated[str, StringConstraints(max_length=40)]], Field(max_length=10)]
        | None
    ) = None
    due_at: PositiveId | None = None
    clear_due_at: bool = False


TOOLS: dict[str, tuple[type[Arguments], str, bool]] = {
    "search": (
        Search,
        "Search messages that your workspace account can read. Returns message IDs for fetch.",
        False,
    ),
    "fetch": (Fetch, "Read a message or task by its ID, such as message:123 or task:45.", False),
    "list_channels": (Arguments, "List channels that your workspace account can access.", False),
    "list_users": (
        Members,
        "Find workspace members by name. Use their user_id for mentions or direct messages.",
        False,
    ),
    "list_topics": (Channel, "List topics in a channel that your account can read.", False),
    "get_messages": (
        Messages,
        "Read recent messages in a channel, with an optional topic and older-message anchor.",
        False,
    ),
    "get_task_board": (
        Arguments,
        "Read the task board and the tasks that your account can access.",
        False,
    ),
    "get_me": (Arguments, "Read the profile of the account that approved this connection.", False),
    "get_direct_messages": (
        DirectMessages,
        "Read recent direct messages with the given members. Use list_users for member IDs.",
        False,
    ),
    "get_needs": (
        GetNeeds,
        "List the Needs you items of this account. Approvals and agent questions are answered in the app, and a reply item ends when its topic is read.",
        False,
    ),
    "send_message": (
        Send,
        "Send a channel message as the approved account. Use mention_user_ids to notify members. Content supports workspace Markdown.",
        True,
    ),
    "send_direct_message": (
        DirectMessage,
        "Send a direct message as the approved account. Use list_users for recipient IDs. Mention IDs notify members.",
        True,
    ),
    "edit_message": (
        EditMessage,
        "Replace the content of a message that the approved account sent.",
        True,
    ),
    "add_reaction": (
        Reaction,
        "Add an emoji reaction to a message, using an emoji name such as thumbs_up.",
        True,
    ),
    "remove_reaction": (
        Reaction,
        "Remove the approved account's emoji reaction from a message.",
        True,
    ),
    "upload_file": (
        UploadFile,
        "Upload a file as the approved account, up to the smaller of the workspace's upload limit and 15 MiB. content_base64 is plain base64 of the file bytes; a data: URL prefix and line breaks are accepted. Returns its url and a markdown link to paste into a message; images display inline.",
        True,
    ),
    "resolve_needs": (
        NeedIds,
        "Mark mentions and card items as done, like the Mark done and Mark as seen buttons. Other kinds of item fail.",
        True,
    ),
    "reopen_needs": (
        NeedIds,
        "Undo resolve_needs for the given items. A card item reopens only within a few minutes.",
        True,
    ),
    "snooze_needs": (
        NeedIds,
        "Hide the given mentions, card items and replies until the next 07:00 on the workspace clock.",
        True,
    ),
    "unsnooze_needs": (NeedIds, "Show snoozed mentions, card items and replies again.", True),
    "delete_task": (TaskId, "Delete a task that the approved account can modify.", True),
    "create_task": (
        CreateTask,
        "Create a task as the account that approved this connection.",
        True,
    ),
    "update_task": (
        UpdateTask,
        "Update an accessible task as the account that approved this connection.",
        True,
    ),
}


# Tools that replace or remove something the account made; a client may
# want to confirm them. Every other write tool only adds.
DESTRUCTIVE_TOOLS = frozenset({"delete_task", "edit_message"})
# Calling these again with the same arguments makes one more message, file
# or card. The rest end in the same state however often they run.
NON_IDEMPOTENT_TOOLS = frozenset(
    {"send_message", "send_direct_message", "upload_file", "create_task"}
)


def tool_list(scopes: list[str], only: Collection[str] | None = None) -> list[dict[str, object]]:
    return [
        {
            "name": name,
            "description": description,
            "inputSchema": model.model_json_schema(),
            "securitySchemes": [
                {
                    "type": "oauth2",
                    "scopes": ["team:read", "team:write"] if write else ["team:read"],
                }
            ],
            "annotations": {
                "readOnlyHint": not write,
                "destructiveHint": name in DESTRUCTIVE_TOOLS,
                "idempotentHint": name not in NON_IDEMPOTENT_TOOLS,
                "openWorldHint": False,
            },
        }
        for name, (model, description, write) in TOOLS.items()
        if (not write or "team:write" in scopes) and (only is None or name in only)
    ]


@sensitive_variables()
def _view(
    request: HttpRequest,
    user: UserProfile,
    view: Any,
    data: dict[str, object],
    files: dict[str, SimpleUploadedFile] | None = None,
    **path: object,
) -> dict[str, Any]:
    tool_request = copy(request)
    tool_request.user = user
    notes = copy(RequestNotes.get_notes(request))
    notes.processed_parameters = set()
    RequestNotes.set_notes(tool_request, notes)
    form = QueryDict("", mutable=True)
    for name, value in data.items():
        if value is not None:
            form[name] = value if isinstance(value, str) else json.dumps(value)
    form._mutable = False
    tool_request.POST = cast("_ImmutableQueryDict", form)
    tool_request.GET = cast("_ImmutableQueryDict", form)
    tool_request._files = MultiValueDict({name: [file] for name, file in (files or {}).items()})  # type: ignore[attr-defined]  # Django caches parsed uploads here.
    response = view(tool_request, user, **path)
    payload = json.loads(response.content)
    if response.status_code != 200 or payload.get("result") == "error":
        raise ValueError("The workspace rejected this operation.")
    return {key: value for key, value in payload.items() if key not in {"result", "msg"}}


@sensitive_variables()
def message_content(request: HttpRequest, user: UserProfile, args: dict[str, object]) -> str:
    content = str(args["content"])
    mentions = cast(list[int], args.get("mention_user_ids", []))
    if mentions:
        result = _view(request, user, users.get_members_backend, {"user_ids": mentions})
        available = {
            member["user_id"] for member in result["members"] if member.get("is_active", False)
        }
        if not set(mentions) <= available:
            raise ValueError("A mention is not available to this account.")
        content = (
            " ".join(f"@**|{user_id}**" for user_id in dict.fromkeys(mentions)) + "\n" + content
        )
    if len(content) > 10000:
        raise ValueError("The message exceeds its limit.")
    return content


def _need_url(user: UserProfile, item: dict[str, Any]) -> str:
    raw_id = item["id"].partition(":")[2]
    if item["kind"] == "mention":
        return user.realm.url + f"/#narrow/id/{raw_id}"
    if item["kind"] == "reply" and item.get("reply") is not None:
        return user.realm.url + f"/#narrow/id/{item['reply']['first_unread_message_id']}"
    if item["task"] is not None:
        return user.realm.url + f"/#tasks/{item['task']['id']}"
    return user.realm.url + "/#needs"


def _need_summary(user: UserProfile, item: dict[str, Any]) -> dict[str, Any]:
    """The part of a Needs you item that a client needs. It leaves out the
    values that approve or reject, which only the app uses."""
    reply = item.get("reply")
    return {
        "id": item["id"],
        "kind": item["kind"],
        "time": item["time"],
        "from": item["actor"]["name"],
        "channel_id": item["stream_id"],
        "topic": item["topic"],
        "text": item["raw_text"],
        "url": _need_url(user, item),
        "task": item["task"],
        "reply": reply,
        "team_wide": item["broadcast"],
        "expired": item["expired"],
        "expires_at": item["expires_at"],
        "resolved_at": item["resolved_at"],
        "resolved_action": item["resolved_action"],
        "can_mark_done": item["resolved_at"] is None
        and item["kind"] in {"mention", "card_mention", "card_assigned"},
    }


@sensitive_variables()
def decode_upload(user: UserProfile, content_base64: str) -> bytes:
    limit_mebibytes = min(user.realm.get_max_file_upload_size_mebibytes(), MAX_FILE_MEBIBYTES)
    encoded = DATA_URL_PREFIX.sub("", content_base64.strip(), count=1)
    encoded = "".join(encoded.split())
    encoded = encoded.rstrip("=")
    # Clients often drop the padding; the length without it still fixes the size.
    if len(encoded) % 4 == 1:
        raise ToolInputError(
            "content_base64 is not valid base64: its length cannot come from any file."
        )
    if len(encoded) * 3 // 4 > limit_mebibytes * 1024 * 1024:
        raise ToolInputError(
            f"The file is larger than the upload limit of {limit_mebibytes} MiB for this connection."
        )
    try:
        return base64.b64decode(encoded + "=" * (-len(encoded) % 4), validate=True)
    except binascii.Error:
        raise ToolInputError(
            "content_base64 is not valid base64: send the file bytes as standard base64 text."
        ) from None


@sensitive_variables()
def execute_tool(
    request: HttpRequest, user: UserProfile, name: str, args: dict[str, object]
) -> dict[str, Any]:
    if name == "list_channels":
        return _view(request, user, streams.get_streams_backend, {"include_web_public": False})
    if name == "list_users":
        result = _view(request, user, users.get_members_backend, {})
        query = str(args["query"]).casefold()
        limit = cast(int, args["limit"])
        members = [
            {
                "user_id": member["user_id"],
                "full_name": member["full_name"],
                "is_bot": member["is_bot"],
            }
            for member in result["members"]
            if member.get("is_active", False)
            and "full_name" in member
            and query in member["full_name"].casefold()
        ]
        return {"users": members[:limit], "has_more": len(members) > limit}
    if name == "list_topics":
        return _view(request, user, streams.get_topics_backend, {}, stream_id=args["channel_id"])
    if name == "get_task_board":
        return _view(request, user, tasks.get_task_board, {})
    if name == "search":
        result = _view(
            request,
            user,
            get_messages_backend,
            {
                "anchor": "newest",
                "num_before": 30,
                "num_after": 0,
                "apply_markdown": False,
                "narrow": [{"operator": "search", "operand": args["query"]}],
            },
        )
        return {
            "results": [
                {
                    "id": f"message:{message['id']}",
                    "title": f"{message['sender_full_name']} · {message['subject']}",
                    "url": user.realm.url + f"/#narrow/id/{message['id']}",
                }
                for message in result["messages"]
            ]
        }
    if name == "fetch":
        kind, raw_id = str(args["id"]).split(":")
        item_id = int(raw_id)
        if kind == "task":
            try:
                task = access_task_by_id(user, item_id)
            except JsonableError as error:
                raise NotAccessibleError(error.msg) from None
            return {
                "id": args["id"],
                "title": task.title,
                "text": task.body,
                "url": user.realm.url + f"/#tasks/{task.id}",
                "metadata": task.to_api_dict(),
            }
        try:
            access_message(user, item_id, is_modifying_message=False)
        except JsonableError as error:
            raise NotAccessibleError(error.msg) from None
        result = _view(
            request, user, get_messages_backend, {"message_ids": [item_id], "apply_markdown": False}
        )
        if not result["messages"]:
            raise JsonableError("Message is not available.")
        message = result["messages"][0]
        return {
            "id": args["id"],
            "title": f"{message['sender_full_name']} · {message['subject']}",
            "text": message["content"],
            "url": user.realm.url + f"/#narrow/id/{item_id}",
            "metadata": {"sender_id": message["sender_id"], "timestamp": message["timestamp"]},
        }
    if name == "get_messages":
        narrow = [{"operator": "channel", "operand": args["channel_id"]}]
        if args.get("topic") is not None:
            narrow.append({"operator": "topic", "operand": str(args["topic"])})
        return _view(
            request,
            user,
            get_messages_backend,
            {
                "anchor": str(args.get("before") or "newest"),
                "num_before": args["limit"],
                "num_after": 0,
                "include_anchor": args.get("before") is None,
                "apply_markdown": False,
                "narrow": narrow,
            },
        )
    if name == "get_needs":
        result = _view(
            request, user, needs.list_needs, {"kind": args["kind"], "status": args["status"]}
        )
        items = [
            item for item in result["items"] if args["include_team_wide"] or not item["broadcast"]
        ]
        limit = cast(int, args["limit"])
        return {
            "items": [_need_summary(user, item) for item in items[:limit]],
            "counts": result["counts"],
            "team_wide_counts": result["broadcast_counts"],
            "has_more": len(items) > limit,
        }
    if name in {"resolve_needs", "reopen_needs", "snooze_needs", "unsnooze_needs"}:
        needs_view = {
            "resolve_needs": needs.resolve_needs,
            "reopen_needs": needs.unresolve_needs,
            "snooze_needs": needs.snooze_needs,
            "unsnooze_needs": needs.unsnooze_needs,
        }[name]
        result = _view(request, user, needs_view, {"need_ids": args["need_ids"]})
        if name == "unsnooze_needs":
            return {"unsnoozed": args["need_ids"]}
        if name == "snooze_needs":
            return {"until": result["until"]}
        return {"done": result["done"], "failed": result["failed"]}
    if name == "send_message":
        return _view(
            request,
            user,
            send_message_backend,
            {
                "type": "stream",
                "to": args["channel_id"],
                "topic": args["topic"],
                "content": message_content(request, user, args),
            },
        )
    if name == "send_direct_message":
        return _view(
            request,
            user,
            send_message_backend,
            {
                "type": "direct",
                "to": args["recipient_user_ids"],
                "content": message_content(request, user, args),
            },
        )
    if name == "get_me":
        result = _view(request, user, users.get_profile_backend, {})
        return {
            key: result[key]
            for key in ("user_id", "full_name", "is_admin", "is_guest", "role")
            if key in result
        }
    if name == "get_direct_messages":
        return _view(
            request,
            user,
            get_messages_backend,
            {
                "anchor": str(args.get("before") or "newest"),
                "num_before": args["limit"],
                "num_after": 0,
                "include_anchor": args.get("before") is None,
                "apply_markdown": False,
                "narrow": [{"operator": "dm", "operand": args["user_ids"]}],
            },
        )
    if name == "edit_message":
        return _view(
            request,
            user,
            update_message_backend,
            {"content": args["content"]},
            message_id=args["message_id"],
        )
    if name in {"add_reaction", "remove_reaction"}:
        view = reactions.add_reaction if name == "add_reaction" else reactions.remove_reaction
        return _view(
            request,
            user,
            view,
            {"emoji_name": args["emoji_name"]},
            message_id=args["message_id"],
        )
    if name == "upload_file":
        raw = decode_upload(user, str(args["content_base64"]))
        file = SimpleUploadedFile(str(args["filename"]), raw, str(args["content_type"]))
        try:
            result = _view(request, user, upload.upload_file_backend, {}, {"file": file})
        except JsonableError as error:
            raise ToolInputError(error.msg) from None
        return {
            "url": result["url"],
            "filename": result["filename"],
            "markdown": f"[{result['filename']}]({result['url']})",
        }
    if name == "delete_task":
        return _view(request, user, tasks.delete_task, {}, task_id=args["task_id"])
    if name == "create_task":
        return _view(request, user, tasks.create_task, args)
    if name == "update_task":
        data = {key: value for key, value in args.items() if key != "task_id"}
        return _view(request, user, tasks.update_task, data, task_id=args["task_id"])
    raise ValueError("Unknown tool.")
