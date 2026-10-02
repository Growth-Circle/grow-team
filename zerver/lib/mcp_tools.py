"""MCP tools use the workspace's existing permission checks."""

import json
from copy import copy
from typing import TYPE_CHECKING, Annotated, Any, cast

from django.http import HttpRequest, QueryDict
from django.views.decorators.debug import sensitive_variables
from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from zerver.lib.exceptions import JsonableError
from zerver.lib.message import access_message
from zerver.lib.request import RequestNotes
from zerver.lib.tasks import access_task_by_id
from zerver.models import UserProfile
from zerver.views import streams, tasks, users
from zerver.views.message_fetch import get_messages_backend
from zerver.views.message_send import send_message_backend

if TYPE_CHECKING:
    from django.http.request import _ImmutableQueryDict

PositiveId = Annotated[int, Field(strict=True, gt=0)]
ShortText = Annotated[str, StringConstraints(min_length=1, max_length=200)]


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


class UpdateTask(Arguments):
    task_id: PositiveId
    title: Annotated[str, StringConstraints(min_length=1, max_length=200)] | None = None
    column_id: PositiveId | None = None
    body: Annotated[str, StringConstraints(max_length=10000)] | None = None
    assignee_id: PositiveId | None = None
    blocked: bool | None = None


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


def tool_list(scopes: list[str]) -> list[dict[str, object]]:
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
                "destructiveHint": False,
                "idempotentHint": not write,
                "openWorldHint": False,
            },
        }
        for name, (model, description, write) in TOOLS.items()
        if not write or "team:write" in scopes
    ]


@sensitive_variables()
def _view(
    request: HttpRequest, user: UserProfile, view: Any, data: dict[str, object], **path: object
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
            task = access_task_by_id(user, item_id)
            return {
                "id": args["id"],
                "title": task.title,
                "text": task.body,
                "url": user.realm.url + f"/#tasks/{task.id}",
                "metadata": task.to_api_dict(),
            }
        access_message(user, item_id, is_modifying_message=False)
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
    if name == "create_task":
        return _view(request, user, tasks.create_task, args)
    if name == "update_task":
        data = {key: value for key, value in args.items() if key != "task_id"}
        return _view(request, user, tasks.update_task, data, task_id=args["task_id"])
    raise ValueError("Unknown tool.")
