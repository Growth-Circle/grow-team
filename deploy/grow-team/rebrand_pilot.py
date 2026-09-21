"""Apply the Grow Team rebrand to verified, untouched pilot seed content.

Run this script in the deployed application container.  It audits by default.
Pass --apply only after the audit reports the expected pilot data.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from zerver.models import Message, Realm, Stream

EXPECTED_HOST = "team.growc.id"
EXPECTED_REALM_ID = 2
EXPECTED_REALM_NAME = "Grow Team"
EXPECTED_STREAMS = {
    1: ("Zulip", "Questions and discussion about using Zulip."),
    2: ("sandbox", "Experiment with Zulip here. :test_tube:"),
}
EXPECTED_MESSAGES = {
    3: ("experiments", "d6d1f7392ae07a0439436b6a49c47b54417e9701d89c601eb21a71935900509a"),
    4: ("experiments", "78f56448cdae1ea08ff008d8a52c48b3980342b90e16e0d4d1ef35dd8af97280"),
    10: ("welcome to Zulip!", "7afd8672557b1232d0d83fb35834ae21a6ad30a3b985ba07f8af99eb06c679b1"),
    11: ("welcome to Zulip!", "26b4ae9035e335600a4803b9071e992947f1b6449be09a9568799866cad01425"),
    12: ("welcome to Zulip!", "ef554746c2a0e9791e6755308bea0ad8e6a8831b202a7fd8927746a0f458811e"),
    13: ("\x07", "8dd6cb1950fad0785db8dcc8b2a8bd2c24984456b1c483d4994b01c4c50e83e4"),
}
EXPECTED_MESSAGE_STREAMS = {3: 2, 4: 2, 10: 1, 11: 1, 12: 1}
OLD_VIDEO_PARAGRAPH = (
    "You can always come back to the [Welcome to Zulip video]"
    "(https://static.zulipchat.com/static/navigation-tour-video/zulip-10.mp4) "
    "for a quick app overview."
)
HELP_CENTER_PARAGRAPH = "Use the [help center](/help/) whenever you need a quick app overview."


class PilotDataMismatchError(RuntimeError):
    """The database does not contain the exact pilot data this script may edit."""


@dataclass(frozen=True)
class AuditResult:
    realm_id: int
    stream_ids: tuple[int, ...]
    message_ids: tuple[int, ...]


def content_sha256(content: str) -> str:
    return hashlib.sha256(content.encode()).hexdigest()


def replacement_content(message_id: int, content: str) -> str:
    """Replace display branding only; leave lowercase URLs and protocol names unchanged."""
    if message_id == 13:
        if content.count(OLD_VIDEO_PARAGRAPH) != 1:
            raise PilotDataMismatchError(
                "Message 13 does not contain the expected welcome-video paragraph"
            )
        content = content.replace(OLD_VIDEO_PARAGRAPH, HELP_CENTER_PARAGRAPH)
    return content.replace("Zulip", "Grow Team")


def _audit_locked() -> tuple[Realm, list[Stream], list[Message]]:
    """Return locked pilot rows or raise before any change is made."""
    from django.conf import settings
    from django.db.models import Q

    from zerver.models import Message, Realm, Stream, UserProfile
    from zerver.models.recipients import Recipient

    if settings.EXTERNAL_HOST != EXPECTED_HOST:
        raise PilotDataMismatchError(
            f"EXTERNAL_HOST is {settings.EXTERNAL_HOST!r}, expected {EXPECTED_HOST!r}"
        )

    realm = Realm.objects.select_for_update().filter(id=EXPECTED_REALM_ID).first()
    if realm is None:
        raise PilotDataMismatchError(f"Realm {EXPECTED_REALM_ID} does not exist")
    if realm.string_id != "" or realm.name != EXPECTED_REALM_NAME:
        raise PilotDataMismatchError("Realm 2 is not the expected root Grow Team realm")
    if (
        Realm.objects.select_for_update()
        .exclude(id=realm.id)
        .filter(Q(string_id="") | Q(name=EXPECTED_REALM_NAME))
        .exists()
    ):
        raise PilotDataMismatchError("Found a conflicting root realm or Grow Team realm name")
    if (
        Stream.objects.select_for_update()
        .filter(realm=realm, name__iexact="Grow Team")
        .exclude(id=1)
        .exists()
    ):
        raise PilotDataMismatchError("Found another pilot-realm stream named Grow Team")

    welcome_bots = list(
        UserProfile.objects.select_for_update().filter(
            email__iexact=settings.WELCOME_BOT, is_bot=True
        )
    )
    if len(welcome_bots) != 1:
        raise PilotDataMismatchError("Could not identify exactly one Welcome Bot")
    welcome_bot = welcome_bots[0]

    streams = list(
        Stream.objects.select_for_update()
        .filter(realm=realm, id__in=EXPECTED_STREAMS)
        .order_by("id")
    )
    if {stream.id for stream in streams} != set(EXPECTED_STREAMS):
        raise PilotDataMismatchError(
            "The expected seed streams do not both belong to the pilot realm"
        )
    for stream in streams:
        if (stream.name, stream.description) != EXPECTED_STREAMS[stream.id]:
            raise PilotDataMismatchError(
                f"Stream {stream.id} differs from the expected untouched seed stream"
            )

    messages = list(
        Message.objects.select_for_update()
        .select_related("sender", "recipient", "realm")
        .filter(realm=realm, id__in=EXPECTED_MESSAGES)
        .order_by("id")
    )
    if {message.id for message in messages} != set(EXPECTED_MESSAGES):
        raise PilotDataMismatchError(
            "The expected seed messages do not all belong to the pilot realm"
        )
    for message in messages:
        expected_subject, expected_hash = EXPECTED_MESSAGES[message.id]
        if message.sender_id != welcome_bot.id or not message.sender.is_bot:
            raise PilotDataMismatchError(
                f"Message {message.id} was not sent by the pilot Welcome Bot"
            )
        if message.last_edit_time is not None or message.edit_history is not None:
            raise PilotDataMismatchError(f"Message {message.id} has been edited")
        if message.subject != expected_subject or content_sha256(message.content) != expected_hash:
            raise PilotDataMismatchError(
                f"Message {message.id} differs from the expected untouched seed message"
            )
        if message.id == 13:
            if message.recipient.type != Recipient.DIRECT_MESSAGE_GROUP:
                raise PilotDataMismatchError("Message 13 is not the expected direct message")
        elif (
            message.recipient.type != Recipient.STREAM
            or message.recipient.type_id != EXPECTED_MESSAGE_STREAMS[message.id]
        ):
            raise PilotDataMismatchError(
                f"Message {message.id} is not the expected channel message"
            )

    return realm, streams, messages


def audit() -> AuditResult:
    """Audit without writing.  This fails closed on any unexpected pilot state."""
    from django.db import transaction

    with transaction.atomic():
        realm, streams, messages = _audit_locked()
        return AuditResult(
            realm.id,
            tuple(stream.id for stream in streams),
            tuple(message.id for message in messages),
        )


def run(*, apply: bool = False) -> AuditResult:
    """Audit the pilot data, and apply the bounded rebrand only when requested."""
    if not apply:
        result = audit()
        print(
            f"Audit passed: realm {result.realm_id}; streams {result.stream_ids}; messages {result.message_ids}."
        )
        return result

    from django.db import transaction

    from zerver.lib.cache import flush_message, flush_stream
    from zerver.lib.markdown import render_message_markdown
    from zerver.lib.markdown import version as markdown_version
    from zerver.lib.streams import render_stream_description

    with transaction.atomic():
        realm, streams, messages = _audit_locked()
        for stream in streams:
            stream.name = "Grow Team" if stream.id == 1 else stream.name
            stream.description = stream.description.replace("Zulip", "Grow Team")
            stream.rendered_description = render_stream_description(stream.description, realm)
            stream.save(update_fields=["name", "description", "rendered_description"])

        for message in messages:
            message.content = replacement_content(message.id, message.content)
            if message.id in (10, 11, 12):
                message.subject = "welcome to Grow Team!"
            rendering_result = render_message_markdown(message, message.content, realm=realm)
            message.rendered_content = rendering_result.rendered_content
            message.rendered_content_version = markdown_version
            message.save(
                update_fields=["subject", "content", "rendered_content", "rendered_content_version"]
            )

        transaction.on_commit(
            lambda: (
                [flush_stream(instance=stream) for stream in streams]
                + [flush_message(instance=message) for message in messages]
            )
        )
        result = AuditResult(
            realm.id,
            tuple(stream.id for stream in streams),
            tuple(message.id for message in messages),
        )

    print(
        f"Applied: realm {result.realm_id}; streams {result.stream_ids}; messages {result.message_ids}."
    )
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Apply the audited updates.")
    args = parser.parse_args()

    sys.path.insert(0, str(Path("/home/zulip/deployments/current").resolve()))
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "zproject.settings")
    import django

    django.setup()
    run(apply=args.apply)


if __name__ == "__main__":
    main()
