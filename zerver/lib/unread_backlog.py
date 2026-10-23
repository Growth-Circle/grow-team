"""Unread messages that have waited longer than a week, for the button that
clears the backlog on the Inbox and Needs you pages. The person decides
when to use it, and it only ever touches their own unread messages."""

from datetime import timedelta

from django.db.models import QuerySet
from django.utils.timezone import now as timezone_now

from zerver.actions.message_flags import do_update_message_flags
from zerver.models import UserMessage, UserProfile

STALE_UNREAD_DAYS = 7
BATCH_SIZE = 2000


def stale_unread_user_messages(user_profile: UserProfile) -> QuerySet[UserMessage]:
    cutoff = timezone_now() - timedelta(days=STALE_UNREAD_DAYS)
    return UserMessage.objects.filter(
        user_profile=user_profile, message__date_sent__lt=cutoff
    ).extra(  # noqa: S610
        where=[UserMessage.where_unread()]
    )


def count_stale_unread(user_profile: UserProfile) -> int:
    return stale_unread_user_messages(user_profile).count()


def mark_stale_unread_as_read(user_profile: UserProfile) -> int:
    marked_count = 0
    while True:
        message_ids = list(
            stale_unread_user_messages(user_profile)
            .order_by("message_id")
            .values_list("message_id", flat=True)[:BATCH_SIZE]
        )
        if not message_ids:
            return marked_count
        updated_count, _ignored = do_update_message_flags(user_profile, "add", "read", message_ids)
        marked_count += updated_count
