"""The room topics list: the 50 most recently active topics in a room,
each with its message count, from one aggregate query (spec 01 RM-11..16;
PLAN.md WP14 step 7, kontrak 5.3)."""

from typing import Any

from django.db import connection

from zerver.models import Stream, UserProfile

MAX_TOPICS = 50


def get_room_topics(user_profile: UserProfile, stream: Stream) -> list[dict[str, Any]]:
    """50 most recent topics with `message_count`, via one aggregate
    query. Topics that differ only in case are one row here, the same as
    Zulip's own topic list (zerver.lib.topic.get_topic_history_for_stream):
    unlike that helper, this one applies LIMIT in SQL, so the merge has to
    happen in the GROUP BY itself, not in a later Python pass over an
    already-truncated result (WP14 review defect 6). Grouping by
    `upper(subject)` matches the zerver_message_realm_recipient_upper_
    subject index; `array_agg(subject ORDER BY id DESC))[1]` keeps the
    most recently used casing as the display name.

    A room with protected history counts only the caller's own
    UserMessage rows.
    """
    assert stream.recipient_id is not None
    cursor = connection.cursor()
    if stream.is_history_public_to_subscribers():
        query = """
        SELECT
            (array_agg("zerver_message"."subject" ORDER BY "zerver_message"."id" DESC))[1]
                AS topic,
            max("zerver_message"."id") AS max_id,
            count(*) AS message_count
        FROM "zerver_message"
        WHERE (
            "zerver_message"."realm_id" = %s AND
            "zerver_message"."recipient_id" = %s AND
            "zerver_message"."is_channel_message"
        )
        GROUP BY upper("zerver_message"."subject")
        ORDER BY max_id DESC
        LIMIT %s
        """
        params: list[int] = [user_profile.realm_id, stream.recipient_id, MAX_TOPICS]
    else:
        query = """
        SELECT
            (array_agg("zerver_message"."subject" ORDER BY "zerver_message"."id" DESC))[1]
                AS topic,
            max("zerver_message"."id") AS max_id,
            count(*) AS message_count
        FROM "zerver_message"
        INNER JOIN "zerver_usermessage" ON (
            "zerver_usermessage"."message_id" = "zerver_message"."id"
        )
        WHERE (
            "zerver_usermessage"."user_profile_id" = %s AND
            "zerver_message"."realm_id" = %s AND
            "zerver_message"."recipient_id" = %s AND
            "zerver_message"."is_channel_message"
        )
        GROUP BY upper("zerver_message"."subject")
        ORDER BY max_id DESC
        LIMIT %s
        """
        params = [user_profile.id, user_profile.realm_id, stream.recipient_id, MAX_TOPICS]
    cursor.execute(query, params)
    rows = cursor.fetchall()
    cursor.close()
    return [
        {"name": topic, "max_id": max_id, "message_count": message_count}
        for topic, max_id, message_count in rows
    ]
