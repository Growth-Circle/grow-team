from typing import Annotated, Literal

from django.db.models import Q
from django.http import HttpRequest, HttpResponse
from pydantic import Json, StringConstraints

from zerver.actions.playbooks import do_create_playbook, do_edit_playbook, do_set_playbook_archived
from zerver.lib.playbooks import (
    access_playbook_by_id,
    access_playbook_revision,
    check_can_use_playbooks,
    playbook_api_dict,
    readable_playbooks,
    render_playbook_content,
    unified_revision_diff,
    user_can_archive_playbook,
    user_can_edit_playbook,
)
from zerver.lib.response import json_success
from zerver.lib.typed_endpoint import PathOnly, typed_endpoint
from zerver.models import Playbook, PlaybookRevision, UserProfile

PlaybookTitle = Annotated[
    str,
    StringConstraints(min_length=1, max_length=Playbook.MAX_TITLE_LENGTH, strip_whitespace=True),
]
PlaybookContent = Annotated[str, StringConstraints(max_length=PlaybookRevision.MAX_CONTENT_LENGTH)]
RevisionSummary = Annotated[
    str,
    StringConstraints(max_length=PlaybookRevision.MAX_SUMMARY_LENGTH, strip_whitespace=True),
]


def playbook_detail_data(user_profile: UserProfile, playbook: Playbook) -> dict[str, object]:
    return {
        **playbook_api_dict(playbook, include_content=True),
        "can_edit": user_can_edit_playbook(user_profile, playbook),
        "can_archive": user_can_archive_playbook(user_profile, playbook),
    }


@typed_endpoint
def list_playbooks(
    request: HttpRequest,
    user_profile: UserProfile,
    *,
    archived: Json[bool] = False,
    scope: Literal["shared", "personal"] | None = None,
    search: str | None = None,
) -> HttpResponse:
    playbooks = readable_playbooks(user_profile).filter(archived=archived)
    if scope is not None:
        playbooks = playbooks.filter(scope=scope)
    if search is not None and search.strip():
        term = search.strip()
        playbooks = playbooks.filter(
            Q(title__icontains=term) | Q(latest_revision__content__icontains=term)
        )
    playbooks = playbooks.select_related("latest_revision").order_by(
        "-latest_revision__date_created", "-id"
    )
    return json_success(
        request, data={"playbooks": [playbook_api_dict(playbook) for playbook in playbooks]}
    )


@typed_endpoint
def create_playbook(
    request: HttpRequest,
    user_profile: UserProfile,
    *,
    content: PlaybookContent,
    scope: Literal["shared", "personal"] = "shared",
    summary: RevisionSummary = "",
    title: PlaybookTitle,
) -> HttpResponse:
    playbook = do_create_playbook(
        user_profile=user_profile, title=title, content=content, scope=scope, summary=summary
    )
    return json_success(request, data={"playbook": playbook_detail_data(user_profile, playbook)})


@typed_endpoint
def render_playbook_preview(
    request: HttpRequest, user_profile: UserProfile, *, content: PlaybookContent
) -> HttpResponse:
    check_can_use_playbooks(user_profile)
    rendered_content = render_playbook_content(
        content=content, realm=user_profile.realm, author=user_profile
    )
    return json_success(request, data={"rendered_content": rendered_content})


@typed_endpoint
def get_playbook(
    request: HttpRequest, user_profile: UserProfile, *, playbook_id: PathOnly[int]
) -> HttpResponse:
    playbook = access_playbook_by_id(user_profile, playbook_id)
    revisions = playbook.revisions.order_by("-number")
    return json_success(
        request,
        data={
            "playbook": playbook_detail_data(user_profile, playbook),
            "revisions": [revision.to_api_dict(include_content=False) for revision in revisions],
        },
    )


@typed_endpoint
def update_playbook(
    request: HttpRequest,
    user_profile: UserProfile,
    *,
    base_revision: Json[int],
    content: PlaybookContent | None = None,
    playbook_id: PathOnly[int],
    summary: RevisionSummary = "",
    title: PlaybookTitle | None = None,
) -> HttpResponse:
    playbook = do_edit_playbook(
        user_profile=user_profile,
        playbook_id=playbook_id,
        base_revision=base_revision,
        title=title,
        content=content,
        summary=summary,
    )
    return json_success(request, data={"playbook": playbook_detail_data(user_profile, playbook)})


@typed_endpoint
def archive_playbook(
    request: HttpRequest, user_profile: UserProfile, *, playbook_id: PathOnly[int]
) -> HttpResponse:
    playbook = do_set_playbook_archived(
        user_profile=user_profile, playbook_id=playbook_id, archived=True
    )
    return json_success(request, data={"playbook": playbook_detail_data(user_profile, playbook)})


@typed_endpoint
def unarchive_playbook(
    request: HttpRequest, user_profile: UserProfile, *, playbook_id: PathOnly[int]
) -> HttpResponse:
    playbook = do_set_playbook_archived(
        user_profile=user_profile, playbook_id=playbook_id, archived=False
    )
    return json_success(request, data={"playbook": playbook_detail_data(user_profile, playbook)})


@typed_endpoint
def get_playbook_revision(
    request: HttpRequest,
    user_profile: UserProfile,
    *,
    playbook_id: PathOnly[int],
    revision_number: PathOnly[int],
) -> HttpResponse:
    playbook = access_playbook_by_id(user_profile, playbook_id)
    revision = access_playbook_revision(playbook, revision_number)
    return json_success(request, data={"revision": revision.to_api_dict(include_content=True)})


@typed_endpoint
def get_playbook_diff(
    request: HttpRequest,
    user_profile: UserProfile,
    *,
    from_revision: Json[int],
    playbook_id: PathOnly[int],
    to_revision: Json[int],
) -> HttpResponse:
    playbook = access_playbook_by_id(user_profile, playbook_id)
    older = access_playbook_revision(playbook, from_revision)
    newer = access_playbook_revision(playbook, to_revision)
    return json_success(
        request,
        data={
            "from_revision": older.number,
            "to_revision": newer.number,
            "diff": unified_revision_diff(older, newer),
        },
    )
