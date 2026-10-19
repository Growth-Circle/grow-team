"""HTTP surface for the Perlu kamu inbox (spec 03). Listing is read-only;
the only write here marks a mention done, or undoes that."""

from typing import Literal

from django.http import HttpRequest, HttpResponse
from pydantic import Json

from zerver.lib import needs
from zerver.lib.typed_endpoint import typed_endpoint
from zerver.models import UserProfile
from zerver.views.agents import _success, safe_agent_endpoint


@safe_agent_endpoint
@typed_endpoint
def list_needs(
    request: HttpRequest,
    user_profile: UserProfile,
    *,
    kind: Literal["approval", "mention", "decision", "card_mention", "card_assigned", "reply"]
    | None = None,
    status: Literal["open", "resolved"] = "open",
    since: Literal["today"] | None = None,
) -> HttpResponse:
    result = needs.list_needs(user_profile, kind=kind, status=status, since=since)
    return _success(request, dict(result))


@safe_agent_endpoint
def resolve_card_notification(
    request: HttpRequest, user_profile: UserProfile, notification_id: int
) -> HttpResponse:
    needs.resolve_card_notification(user_profile, notification_id)
    return _success(request)


@safe_agent_endpoint
def resolve_mention(
    request: HttpRequest, user_profile: UserProfile, message_id: int
) -> HttpResponse:
    if request.method == "DELETE":
        needs.unresolve_mention(user_profile, message_id)
    else:
        needs.resolve_mention(user_profile, message_id)
    return _success(request)


@safe_agent_endpoint
@typed_endpoint
def resolve_needs(
    request: HttpRequest, user_profile: UserProfile, *, need_ids: Json[list[str]]
) -> HttpResponse:
    return _success(request, dict(needs.resolve_many(user_profile, need_ids)))


@safe_agent_endpoint
@typed_endpoint
def unresolve_needs(
    request: HttpRequest, user_profile: UserProfile, *, need_ids: Json[list[str]]
) -> HttpResponse:
    return _success(request, dict(needs.unresolve_many(user_profile, need_ids)))


@safe_agent_endpoint
@typed_endpoint
def snooze_needs(
    request: HttpRequest, user_profile: UserProfile, *, need_ids: Json[list[str]]
) -> HttpResponse:
    until = needs.snooze_many(user_profile, need_ids)
    return _success(request, {"until": until.isoformat()})


@safe_agent_endpoint
@typed_endpoint
def unsnooze_needs(
    request: HttpRequest, user_profile: UserProfile, *, need_ids: Json[list[str]]
) -> HttpResponse:
    needs.unsnooze_many(user_profile, need_ids)
    return _success(request)
