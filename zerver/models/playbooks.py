"""Playbooks: reusable Markdown documents that describe how the team does a
piece of work. Every edit adds a revision, and nothing is overwritten, so
the history of a playbook is complete."""

from typing import Any

from django.db import models
from django.db.models import Q
from django.utils.timezone import now as timezone_now

from zerver.models.realms import Realm
from zerver.models.users import UserProfile

PLAYBOOK_SCOPE_SHARED = "shared"
PLAYBOOK_SCOPE_PERSONAL = "personal"

PLAYBOOK_SCOPES = [PLAYBOOK_SCOPE_SHARED, PLAYBOOK_SCOPE_PERSONAL]


class Playbook(models.Model):
    """A shared playbook belongs to the whole workspace: every member
    except a guest can read and edit it. A personal playbook is for its
    owner alone. The owner of a shared playbook is the member who made
    it."""

    MAX_TITLE_LENGTH = 100
    MAX_SLUG_LENGTH = 80

    SHARED = PLAYBOOK_SCOPE_SHARED
    PERSONAL = PLAYBOOK_SCOPE_PERSONAL

    realm = models.ForeignKey(Realm, on_delete=models.CASCADE)
    title = models.CharField(max_length=MAX_TITLE_LENGTH)
    slug = models.CharField(max_length=MAX_SLUG_LENGTH)
    scope = models.CharField(max_length=10, choices=[(scope, scope) for scope in PLAYBOOK_SCOPES])
    owner = models.ForeignKey(UserProfile, on_delete=models.CASCADE, related_name="+")
    date_created = models.DateTimeField(default=timezone_now)
    archived = models.BooleanField(default=False)
    # The revision that readers and agents get. It is set in the same
    # transaction that adds the revision, so it is only null while a
    # playbook is being created.
    latest_revision = models.ForeignKey(
        "PlaybookRevision", on_delete=models.SET_NULL, null=True, related_name="+"
    )

    class Meta:
        ordering = ["-id"]
        constraints = [
            models.CheckConstraint(
                condition=Q(scope__in=PLAYBOOK_SCOPES), name="playbook_scope_valid"
            ),
            # A slug names one playbook in the shared list. A personal
            # playbook only needs a slug that is free among its owner's
            # own, so that a member never learns what another member
            # keeps to themselves from a slug that is taken.
            models.UniqueConstraint(
                fields=["realm", "slug"],
                condition=Q(scope=PLAYBOOK_SCOPE_SHARED),
                name="playbook_shared_slug_unique",
            ),
            models.UniqueConstraint(
                fields=["realm", "owner", "slug"],
                condition=Q(scope=PLAYBOOK_SCOPE_PERSONAL),
                name="playbook_personal_slug_unique",
            ),
        ]


class PlaybookRevision(models.Model):
    MAX_CONTENT_LENGTH = 100_000
    MAX_SUMMARY_LENGTH = 200

    playbook = models.ForeignKey(Playbook, on_delete=models.CASCADE, related_name="revisions")
    # 1 for the first revision of a playbook, then one more each time.
    number = models.PositiveIntegerField()
    content = models.TextField()
    rendered_content = models.TextField()
    author = models.ForeignKey(UserProfile, on_delete=models.CASCADE, related_name="+")
    date_created = models.DateTimeField(default=timezone_now)
    summary = models.CharField(max_length=MAX_SUMMARY_LENGTH, default="")

    class Meta:
        ordering = ["number"]
        constraints = [
            models.UniqueConstraint(
                fields=["playbook", "number"], name="playbook_revision_number_unique"
            ),
        ]

    def to_api_dict(self, *, include_content: bool) -> dict[str, Any]:
        data: dict[str, Any] = {
            "number": self.number,
            "author_id": self.author_id,
            "date_created": int(self.date_created.timestamp()),
            "summary": self.summary,
        }
        if include_content:
            data["content"] = self.content
            data["rendered_content"] = self.rendered_content
        return data
