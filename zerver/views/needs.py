"""HTTP surface for the Perlu kamu inbox (spec 03). Listing is read-only;
the only write here marks a mention done, or undoes that."""

from typing import Literal

from django.http import HttpRequest, HttpResponse

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
    kind: Literal["approval", "mention", "decision"] | None = None,
    status: Literal["open", "resolved"] = "open",
    since: Literal["today"] | None = None,
) -> HttpResponse:
    result = needs.list_needs(user_profile, kind=kind, status=status, since=since)
    return _success(request, dict(result))


@safe_agent_endpoint
def resolve_mention(
    request: HttpRequest, user_profile: UserProfile, message_id: int
) -> HttpResponse:
    if request.method == "DELETE":
        needs.unresolve_mention(user_profile, message_id)
    else:
        needs.resolve_mention(user_profile, message_id)
    return _success(request)
