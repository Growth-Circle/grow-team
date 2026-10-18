"""Reading, rendering and access rules for playbooks.

A shared playbook is for every member except a guest. A personal playbook
is for its owner alone, and nobody else, an administrator included, can
find out that it exists: every lookup that fails for access fails the same
way as a lookup of an id that is not there.

A later integration that gives an agent its playbooks should read through
`get_latest_playbook_content`, which applies these rules, and keep the
playbook `id`, which does not change when the title or content does.
"""

import difflib
import re

from django.db.models import Q, QuerySet
from django.utils.text import slugify
from django.utils.translation import gettext as _
from typing_extensions import override

from zerver.lib.exceptions import ErrorCode, JsonableError, MarkdownRenderingError
from zerver.lib.markdown import markdown_convert
from zerver.lib.mention import MentionBackend, MentionData
from zerver.models import Playbook, PlaybookRevision, Realm, UserProfile
from zerver.models.users import active_non_guest_user_ids

# Text that is not a Markdown comment: a code span is kept as it is, so
# that a comment in a code span shows. Anything else that starts a
# comment ends at `-->`, or at the end of the block when the author never
# closed it.
_COMMENT_OR_CODE_SPAN = re.compile(
    r"(?P<ticks>`+)(?P<code>.+?)(?P=ticks)|<!--.*?(?:-->|\Z)", re.DOTALL
)
_FENCE_LINE = re.compile(r"^ {0,3}(?P<fence>`{3,}|~{3,})")


class PlaybookRevisionConflictError(JsonableError):
    code = ErrorCode.PLAYBOOK_REVISION_CONFLICT
    http_status_code = 409
    data_fields = ["latest_revision"]

    def __init__(self, latest_revision: int) -> None:
        self.latest_revision = latest_revision

    @staticmethod
    @override
    def msg_format() -> str:
        return _("Someone saved a newer revision of this playbook.")


def strip_html_comments(content: str) -> str:
    """Remove HTML comments from Markdown source, except in code. A skill
    file keeps notes for its authors in comments, and Zulip Markdown
    would print them as text."""
    kept: list[str] = []
    block: list[str] = []
    open_fence: str | None = None

    def flush_block() -> None:
        text = "".join(block)
        kept.append(
            _COMMENT_OR_CODE_SPAN.sub(
                lambda match: match.group(0) if match.group("ticks") else "", text
            )
        )
        block.clear()

    for line in content.splitlines(keepends=True):
        fence = _FENCE_LINE.match(line)
        if open_fence is None and fence is not None:
            flush_block()
            open_fence = fence.group("fence")
            kept.append(line)
        elif open_fence is not None:
            kept.append(line)
            if (
                fence is not None
                and fence.group("fence")[0] == open_fence[0]
                and len(fence.group("fence")) >= len(open_fence)
            ):
                open_fence = None
        else:
            block.append(line)
    flush_block()
    return "".join(kept)


def render_playbook_content(*, content: str, realm: Realm, author: UserProfile) -> str:
    try:
        return markdown_convert(
            strip_html_comments(content),
            message_realm=realm,
            mention_data=MentionData(MentionBackend(realm.id), content, author),
            no_previews=True,
            acting_user=author,
        ).rendered_content
    except MarkdownRenderingError:
        raise JsonableError(_("This playbook could not be displayed. Simplify its Markdown."))


def user_can_use_playbooks(user_profile: UserProfile) -> bool:
    return not user_profile.is_guest


def user_can_read_playbook(user_profile: UserProfile, playbook: Playbook) -> bool:
    if playbook.realm_id != user_profile.realm_id or not user_can_use_playbooks(user_profile):
        return False
    return playbook.scope == Playbook.SHARED or playbook.owner_id == user_profile.id


def user_can_edit_playbook(user_profile: UserProfile, playbook: Playbook) -> bool:
    # A bot reads playbooks to follow them, and does not change them: a
    # playbook steers what agents do.
    return not user_profile.is_bot and user_can_read_playbook(user_profile, playbook)


def user_can_archive_playbook(user_profile: UserProfile, playbook: Playbook) -> bool:
    if not user_can_edit_playbook(user_profile, playbook):
        return False
    if playbook.scope == Playbook.PERSONAL:
        return True
    return user_profile.is_realm_admin or playbook.owner_id == user_profile.id


def check_can_use_playbooks(user_profile: UserProfile) -> None:
    if not user_can_use_playbooks(user_profile):
        raise JsonableError(_("Guests cannot use playbooks."))


def readable_playbooks(user_profile: UserProfile) -> QuerySet[Playbook]:
    check_can_use_playbooks(user_profile)
    return Playbook.objects.filter(realm=user_profile.realm).filter(
        Q(scope=Playbook.SHARED) | Q(scope=Playbook.PERSONAL, owner=user_profile)
    )


def access_playbook_by_id(
    user_profile: UserProfile, playbook_id: int, *, for_update: bool = False
) -> Playbook:
    queryset = readable_playbooks(user_profile).select_related("latest_revision")
    if for_update:
        queryset = queryset.select_for_update(of=("self",))
    try:
        return queryset.get(id=playbook_id)
    except Playbook.DoesNotExist:
        raise JsonableError(_("Playbook does not exist."))


def check_can_edit_playbook(user_profile: UserProfile, playbook: Playbook) -> None:
    if not user_can_edit_playbook(user_profile, playbook):
        raise JsonableError(_("You do not have permission to change this playbook."))


def check_can_archive_playbook(user_profile: UserProfile, playbook: Playbook) -> None:
    if not user_can_archive_playbook(user_profile, playbook):
        raise JsonableError(
            _("Only an administrator or the member who made a shared playbook can archive it.")
        )


def access_playbook_revision(playbook: Playbook, number: int) -> PlaybookRevision:
    try:
        return PlaybookRevision.objects.get(playbook=playbook, number=number)
    except PlaybookRevision.DoesNotExist:
        raise JsonableError(_("Revision does not exist."))


def get_latest_playbook_content(user_profile: UserProfile, playbook_id: int) -> str:
    """The Markdown that an agent acting for `user_profile` follows."""
    playbook = access_playbook_by_id(user_profile, playbook_id)
    assert playbook.latest_revision is not None
    return playbook.latest_revision.content


def playbook_event_audience(playbook: Playbook) -> list[int]:
    if playbook.scope == Playbook.PERSONAL:
        return [playbook.owner_id]
    return sorted(active_non_guest_user_ids(playbook.realm_id))


def unique_playbook_slug(*, realm: Realm, owner: UserProfile, scope: str, title: str) -> str:
    base = slugify(title)[: Playbook.MAX_SLUG_LENGTH] or "playbook"
    taken = Playbook.objects.filter(realm=realm)
    if scope == Playbook.PERSONAL:
        taken = taken.filter(scope=Playbook.PERSONAL, owner=owner)
    else:
        taken = taken.filter(scope=Playbook.SHARED)
    taken_slugs = set(taken.filter(slug__startswith=base).values_list("slug", flat=True))
    slug = base
    suffix = 2
    while slug in taken_slugs:
        tail = f"-{suffix}"
        slug = base[: Playbook.MAX_SLUG_LENGTH - len(tail)] + tail
        suffix += 1
    return slug


def playbook_api_dict(playbook: Playbook, *, include_content: bool = False) -> dict[str, object]:
    latest = playbook.latest_revision
    assert latest is not None
    return {
        "id": playbook.id,
        "title": playbook.title,
        "slug": playbook.slug,
        "scope": playbook.scope,
        "owner_id": playbook.owner_id,
        "archived": playbook.archived,
        "date_created": int(playbook.date_created.timestamp()),
        "latest_revision": latest.to_api_dict(include_content=include_content),
    }


def unified_revision_diff(older: PlaybookRevision, newer: PlaybookRevision) -> str:
    return "\n".join(
        difflib.unified_diff(
            older.content.splitlines(),
            newer.content.splitlines(),
            fromfile=f"revision {older.number}",
            tofile=f"revision {newer.number}",
            lineterm="",
        )
    )
