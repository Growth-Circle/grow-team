"""Write-side operations for playbooks. A change to the content never
replaces a revision: it adds the next one."""

from typing import Any

from django.db import transaction
from django.utils.translation import gettext as _

from zerver.lib.exceptions import JsonableError
from zerver.lib.playbooks import (
    PlaybookRevisionConflictError,
    access_playbook_by_id,
    check_can_archive_playbook,
    check_can_edit_playbook,
    playbook_api_dict,
    playbook_event_audience,
    render_playbook_content,
    unique_playbook_slug,
    user_can_use_playbooks,
)
from zerver.models import Playbook, PlaybookRevision, UserProfile
from zerver.tornado.django_api import send_event_on_commit


def send_playbook_event(playbook: Playbook, op: str) -> None:
    event: dict[str, Any] = {"type": "playbook", "op": op, "playbook": playbook_api_dict(playbook)}
    send_event_on_commit(playbook.realm, event, playbook_event_audience(playbook))


@transaction.atomic(durable=True)
def do_create_playbook(
    *, user_profile: UserProfile, title: str, content: str, scope: str, summary: str = ""
) -> Playbook:
    if user_profile.is_bot or not user_can_use_playbooks(user_profile):
        raise JsonableError(_("You do not have permission to create a playbook."))

    realm = user_profile.realm
    playbook = Playbook.objects.create(
        realm=realm,
        title=title,
        slug=unique_playbook_slug(realm=realm, owner=user_profile, scope=scope, title=title),
        scope=scope,
        owner=user_profile,
    )
    playbook.latest_revision = PlaybookRevision.objects.create(
        playbook=playbook,
        number=1,
        content=content,
        rendered_content=render_playbook_content(content=content, realm=realm, author=user_profile),
        author=user_profile,
        summary=summary,
    )
    playbook.save(update_fields=["latest_revision"])

    send_playbook_event(playbook, "add")
    return playbook


@transaction.atomic(durable=True)
def do_edit_playbook(
    *,
    user_profile: UserProfile,
    playbook_id: int,
    base_revision: int,
    title: str | None,
    content: str | None,
    summary: str,
) -> Playbook:
    playbook = access_playbook_by_id(user_profile, playbook_id, for_update=True)
    check_can_edit_playbook(user_profile, playbook)
    latest = playbook.latest_revision
    assert latest is not None

    # The editor started from `base_revision`. If another member saved
    # since, this save would silently drop their change.
    if base_revision != latest.number:
        raise PlaybookRevisionConflictError(latest.number)

    changed = False
    if title is not None and title != playbook.title:
        playbook.title = title
        playbook.save(update_fields=["title"])
        changed = True
    if content is not None and content != latest.content:
        playbook.latest_revision = PlaybookRevision.objects.create(
            playbook=playbook,
            number=latest.number + 1,
            content=content,
            rendered_content=render_playbook_content(
                content=content, realm=playbook.realm, author=user_profile
            ),
            author=user_profile,
            summary=summary,
        )
        playbook.save(update_fields=["latest_revision"])
        changed = True

    if changed:
        send_playbook_event(playbook, "update")
    return playbook


@transaction.atomic(durable=True)
def do_set_playbook_archived(
    *, user_profile: UserProfile, playbook_id: int, archived: bool
) -> Playbook:
    playbook = access_playbook_by_id(user_profile, playbook_id, for_update=True)
    check_can_archive_playbook(user_profile, playbook)
    if playbook.archived != archived:
        playbook.archived = archived
        playbook.save(update_fields=["archived"])
        send_playbook_event(playbook, "update")
    return playbook
