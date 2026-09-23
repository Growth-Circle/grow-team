"""Executors for the eleven administrator-agent team tools (contract 2.3).

Each executor repeats the same Zulip permission check its matching human
view uses, with the commander (the job requester, `job.requester`) as the
acting user. The agent bot only sends messages, and only after the
commander's own checks pass, so the bot never adds authority the commander
does not already have.
"""

import re
from collections.abc import Callable

from django.db import transaction
from django.utils.translation import gettext as _
from django.utils.translation import override as override_language

from zerver.actions.message_edit import check_update_message
from zerver.actions.message_send import check_message, do_send_messages
from zerver.actions.streams import bulk_add_subscriptions, bulk_remove_subscriptions
from zerver.actions.user_groups import check_add_user_group
from zerver.lib import agent_protocol as p
from zerver.lib.addressee import Addressee
from zerver.lib.exceptions import JsonableError
from zerver.lib.mention import silent_mention_syntax_for_user
from zerver.lib.streams import (
    access_stream_by_id,
    access_stream_for_send_message,
    bulk_can_remove_subscribers_from_streams,
    check_channel_creation_permissions,
    check_for_can_create_topic_group_violation,
    create_stream_if_needed,
    filter_stream_authorization_for_adding_subscribers,
    get_streams_for_user,
)
from zerver.lib.string_validation import check_stream_name
from zerver.lib.topic import RESOLVED_TOPIC_PREFIX, messages_for_topic
from zerver.lib.user_groups import (
    UserGroupMembershipDetails,
    check_user_group_name,
    user_groups_in_realm_serialized,
)
from zerver.lib.users import get_accessible_user_ids, user_ids_to_users
from zerver.models import NamedUserGroup, Realm, Stream, Subscription, UserProfile, agents
from zerver.models.clients import get_client
from zerver.models.groups import get_realm_system_groups_name_dict
from zerver.models.streams import get_stream_by_id_in_realm
from zerver.views.streams import (
    bulk_principals_to_user_profiles,
    send_user_subscribed_and_new_channel_notifications,
    user_directly_controls_user,
)
from zerver.views.user_groups import add_members_to_group_core, remove_members_from_group_core

TeamToolExecutor = Callable[
    [UserProfile, "agents.AgentProfile", p.TeamToolInput], tuple[str, dict[str, object]]
]

_ALWAYS_CONFIRM = {
    "channel.unsubscribe",
    "group.remove_members",
    "topic.move",
    # A permission setting can name this group (contract 2.4 does not list
    # this tool, but it can silently grant channel, group, or command
    # authority; an owner commander must review it like the others).
    "group.add_members",
}

_WHITESPACE_RUN_RE = re.compile(r"\s+")


def _sanitize_receipt_text(text: str) -> str:
    """Collapse whitespace/newlines and defang mentions in a value a model or
    user chose (a search query, a channel/group/topic name, ...), so it
    cannot forge extra "- " lines or pings when the server later posts it
    verbatim in the executed-steps list (contract 2.6 item 7)."""
    collapsed = _WHITESPACE_RUN_RE.sub(" ", text).strip()
    return collapsed.replace("@", "@​")


def team_tool_needs_confirmation(job: "agents.AgentJob", tool_input: p.TeamToolInput) -> bool:
    """Server-side confirmation rule (contract 2.4). The runner never decides this."""
    if tool_input.tool == "team.find":
        return False
    if tool_input.tool in _ALWAYS_CONFIRM:
        return True
    if job.requester_id != job.profile.owner_id:
        return True
    if tool_input.tool == "channel.create":
        return tool_input.is_private
    if tool_input.tool == "channel.subscribe":
        channel = Stream.objects.filter(id=tool_input.channel_id, realm_id=job.realm_id).first()
        return channel is None or channel.invite_only
    if tool_input.tool == "topic.add_person":
        channel = Stream.objects.filter(id=tool_input.channel_id, realm_id=job.realm_id).first()
        if channel is None or not channel.invite_only:
            return False
        subscribed_ids = set(
            Subscription.objects.filter(
                recipient_id=channel.recipient_id,
                user_profile_id__in=tool_input.user_ids,
                active=True,
            ).values_list("user_profile_id", flat=True)
        )
        return not set(tool_input.user_ids) <= subscribed_ids
    return False


def _get_channel(commander: UserProfile, channel_id: int) -> Stream:
    """Look up a channel only through the commander's own access.

    A raw by-ID lookup would return (and let a later error message repeat)
    a private channel's name to a commander who cannot see it at all; Zulip's
    own generic "Invalid channel ID" keeps that name unavailable to anyone
    who does not already have some access to it (contract 2.2 rule 4).
    """
    channel, _sub = access_stream_by_id(commander, channel_id, require_content_access=False)
    return channel


def _latest_message_id_in_topic(channel: Stream, topic_name: str) -> int:
    assert channel.recipient_id is not None
    message_id = (
        messages_for_topic(channel.realm_id, channel.recipient_id, topic_name)
        .order_by("-id")
        .values_list("id", flat=True)
        .first()
    )
    if message_id is None:
        raise JsonableError(_("Topic not found"))
    return message_id


def _channel_label(viewer: UserProfile, channel_id: int) -> str:
    """A channel name for display to `viewer`, never for anyone else.

    This drives the pending-approval summary and the failure summary, both
    of which a job viewer who cannot see a private channel may also see
    (contract 2.2 rule 4); a raw by-ID lookup would leak that channel's
    name to them, so fall back to a description that names nothing.
    """
    try:
        channel, _sub = access_stream_by_id(viewer, channel_id, require_content_access=False)
    except JsonableError:
        return _("a channel you cannot see")
    return f"#{channel.name}"


def _group_label(realm: Realm, group_id: int) -> str:
    group = NamedUserGroup.objects.filter(id=group_id, realm=realm).first()
    return group.name if group is not None else _("group {id}").format(id=group_id)


def _user_names(realm: Realm, user_ids: list[int]) -> str:
    """The people a proposed tool call names, for the approval summary
    (contract: "List the names of the people"), in the order given."""
    names = {
        user.id: user.full_name
        for user in UserProfile.objects.filter(realm=realm, id__in=user_ids)
    }
    return ", ".join(
        names.get(user_id, _("user {id}").format(id=user_id)) for user_id in user_ids
    )


def _find(
    commander: UserProfile, profile: "agents.AgentProfile", tool_input: p.TeamFindInput
) -> tuple[str, dict[str, object]]:
    query = tool_input.query.strip().lower()
    sections = []
    if "person" in tool_input.kinds:
        accessible = set(get_accessible_user_ids(commander.realm, commander))
        matches = list(
            UserProfile.objects.filter(
                realm=commander.realm, is_active=True, is_bot=False, id__in=accessible
            )
            .filter(full_name__icontains=query)
            .order_by("full_name")[:10]
        )
        sections.append(
            _("People: {items}").format(
                items=", ".join(f"{user.full_name} ({user.id})" for user in matches)
                if matches
                else _("none")
            )
        )
    if "channel" in tool_input.kinds:
        matches = [
            stream for stream in get_streams_for_user(commander) if query in stream.name.lower()
        ][:10]
        sections.append(
            _("Channels: {items}").format(
                items=", ".join(
                    f"#{stream.name} ({stream.id}, "
                    f"{_('private') if stream.invite_only else _('public')})"
                    for stream in matches
                )
                if matches
                else _("none")
            )
        )
    if "group" in tool_input.kinds and not commander.is_guest:
        groups = user_groups_in_realm_serialized(
            commander.realm, include_deactivated_groups=False
        ).api_groups
        matches = [group for group in groups if query in group["name"].lower()][:10]
        sections.append(
            _("Groups: {items}").format(
                items=", ".join(f"{group['name']} ({group['id']})" for group in matches)
                if matches
                else _("none")
            )
        )
    summary = _('Search "{query}" — {sections}').format(
        query=tool_input.query, sections="; ".join(sections)
    )
    return summary[:2048], {}


def _channel_create(
    commander: UserProfile, profile: "agents.AgentProfile", tool_input: p.ChannelCreateInput
) -> tuple[str, dict[str, object]]:
    check_stream_name(tool_input.name)
    check_channel_creation_permissions(
        commander,
        is_default_stream=False,
        invite_only=tool_input.is_private,
        is_web_public=False,
        message_retention_days=None,
    )
    channel, created = create_stream_if_needed(
        commander.realm,
        tool_input.name,
        invite_only=tool_input.is_private,
        stream_description=tool_input.description,
        acting_user=commander,
    )
    if not created:
        raise JsonableError(_("A channel with this name already exists."))
    subscribed_ids: list[int] = []
    if tool_input.subscriber_user_ids:
        subscribers = bulk_principals_to_user_profiles(
            sorted(set(tool_input.subscriber_user_ids)), commander
        )
        bulk_add_subscriptions(commander.realm, [channel], subscribers, acting_user=commander)
        send_user_subscribed_and_new_channel_notifications(
            user_profile=commander,
            subscribers=set(),
            new_subscriptions={},
            id_to_user_profile={},
            created_streams=[channel],
            announce=False,
        )
        subscribed_ids = sorted(user.id for user in subscribers)
    kind = _("private") if tool_input.is_private else _("public")
    summary = _("Created {kind} channel #{name} with {count} subscriber(s).").format(
        kind=kind, name=channel.name, count=len(subscribed_ids)
    )
    return summary, {"channel_id": channel.id, "user_ids": subscribed_ids or None}


def _channel_subscribe(
    commander: UserProfile, profile: "agents.AgentProfile", tool_input: p.ChannelSubscribeInput
) -> tuple[str, dict[str, object]]:
    channel = _get_channel(commander, tool_input.channel_id)
    people = bulk_principals_to_user_profiles(sorted(set(tool_input.user_ids)), commander)
    is_subscribing_other_users = any(user.id != commander.id for user in people)
    categorized = filter_stream_authorization_for_adding_subscribers(
        commander, [channel], is_subscribing_other_users
    )
    if categorized.unauthorized_streams or categorized.streams_to_which_user_cannot_add_subscribers:
        # Never repeat the channel's name here: access_stream_by_id already
        # let the commander this far, through metadata-only access that
        # falls short of the stronger access this subscribe check needs
        # (contract 2.2 rule 4 keeps a channel unnamed to a commander who
        # cannot use it).
        raise JsonableError(_("Insufficient permission"))
    subscribed, _already = bulk_add_subscriptions(
        commander.realm, [channel], people, acting_user=commander
    )
    if subscribed:
        send_user_subscribed_and_new_channel_notifications(
            user_profile=commander,
            subscribers=people,
            new_subscriptions={str(item.user.id): [channel.name] for item in subscribed},
            id_to_user_profile={str(item.user.id): item.user for item in subscribed},
            created_streams=[],
            announce=False,
        )
    summary = _(
        "Subscribed {count} of {total} people to #{name} ({already} already there)."
    ).format(
        count=len(subscribed),
        total=len(people),
        name=channel.name,
        already=len(people) - len(subscribed),
    )
    return summary, {
        "channel_id": channel.id,
        "user_ids": sorted(item.user.id for item in subscribed) or None,
    }


def _channel_unsubscribe(
    commander: UserProfile, profile: "agents.AgentProfile", tool_input: p.ChannelUnsubscribeInput
) -> tuple[str, dict[str, object]]:
    channel = _get_channel(commander, tool_input.channel_id)
    people = bulk_principals_to_user_profiles(sorted(set(tool_input.user_ids)), commander)
    unsubscribing_others = any(
        not user_directly_controls_user(commander, target) for target in people
    )
    if unsubscribing_others and not bulk_can_remove_subscribers_from_streams([channel], commander):
        raise JsonableError(_("Insufficient permission"))
    removed, _not_subscribed = bulk_remove_subscriptions(
        commander.realm, people, [channel], acting_user=commander
    )
    summary = _("Unsubscribed {count} of {total} people from #{name}.").format(
        count=len(removed), total=len(people), name=channel.name
    )
    return summary, {
        "channel_id": channel.id,
        "user_ids": sorted(user.id for user, _channel in removed) or None,
    }


def _group_create(
    commander: UserProfile, profile: "agents.AgentProfile", tool_input: p.GroupCreateInput
) -> tuple[str, dict[str, object]]:
    if commander.is_guest:
        raise JsonableError(_("Not allowed for guest users"))
    if not commander.can_create_user_groups():
        raise JsonableError(_("Insufficient permission"))
    commander.realm.ensure_not_on_limited_plan()
    name = check_user_group_name(tool_input.name)
    members = user_ids_to_users(
        tool_input.member_user_ids, commander.realm, allow_deactivated=False, allow_bots=True
    )
    group = check_add_user_group(
        commander.realm, name, members, tool_input.description, acting_user=commander
    )
    summary = _("Created group {name} with {count} member(s).").format(
        name=group.name, count=len(members)
    )
    return summary, {"group_id": group.id, "user_ids": sorted(u.id for u in members) or None}


def _group_add_members(
    commander: UserProfile, profile: "agents.AgentProfile", tool_input: p.GroupAddMembersInput
) -> tuple[str, dict[str, object]]:
    members = sorted(set(tool_input.user_ids))
    # execute_team_tool runs as phase 2 of contract 2.6 item 3, deliberately
    # outside agent_transaction so its chat-table lock stays short; the core
    # helper's select_for_update() needs its own transaction here, since
    # Django raises TransactionManagementError for one run with none open.
    with transaction.atomic(savepoint=False):
        group = add_members_to_group_core(commander, tool_input.group_id, members)
    summary = _("Added {count} member(s) to group {name}.").format(
        count=len(members), name=group.name
    )
    return summary, {"group_id": group.id, "user_ids": members}


def _group_remove_members(
    commander: UserProfile, profile: "agents.AgentProfile", tool_input: p.GroupRemoveMembersInput
) -> tuple[str, dict[str, object]]:
    members = sorted(set(tool_input.user_ids))
    with transaction.atomic(savepoint=False):
        group = remove_members_from_group_core(commander, tool_input.group_id, members)
    summary = _("Removed {count} member(s) from group {name}.").format(
        count=len(members), name=group.name
    )
    return summary, {"group_id": group.id, "user_ids": members}


def _send_check(commander: UserProfile, channel: Stream, topic_name: str) -> None:
    user_group_membership_details = UserGroupMembershipDetails(user_recursive_group_ids=None)
    system_groups_name_dict = get_realm_system_groups_name_dict(channel.realm_id)
    access_stream_for_send_message(
        sender=commander,
        stream=channel,
        forwarder_user_profile=None,
        user_group_membership_details=user_group_membership_details,
        system_groups_name_dict=system_groups_name_dict,
    )
    check_for_can_create_topic_group_violation(
        user_profile=commander,
        stream=channel,
        topic_name=topic_name,
        user_group_membership_details=user_group_membership_details,
        system_groups_name_dict=system_groups_name_dict,
    )


def _topic_post(
    commander: UserProfile, profile: "agents.AgentProfile", tool_input: p.TopicPostInput
) -> tuple[str, dict[str, object]]:
    channel, _sub = access_stream_by_id(commander, tool_input.channel_id)
    _send_check(commander, channel, tool_input.topic)
    client = get_client("Grow Agent")
    message = check_message(
        profile.bot_user,
        client,
        Addressee.for_stream_id(channel.id, tool_input.topic),
        tool_input.content,
        realm=commander.realm,
    )
    message_id = do_send_messages([message])[0].message_id
    summary = _("Posted a message in #{channel} > {topic}.").format(
        channel=channel.name, topic=tool_input.topic
    )
    return summary, {"channel_id": channel.id, "message_id": message_id}


def _topic_add_person(
    commander: UserProfile, profile: "agents.AgentProfile", tool_input: p.TopicAddPersonInput
) -> tuple[str, dict[str, object]]:
    channel, _sub = access_stream_by_id(commander, tool_input.channel_id)
    people = bulk_principals_to_user_profiles(sorted(set(tool_input.user_ids)), commander)
    is_subscribing_other_users = any(user.id != commander.id for user in people)
    categorized = filter_stream_authorization_for_adding_subscribers(
        commander, [channel], is_subscribing_other_users
    )
    if categorized.unauthorized_streams or categorized.streams_to_which_user_cannot_add_subscribers:
        raise JsonableError(_("Insufficient permission"))
    _send_check(commander, channel, tool_input.topic)
    subscribed, _already = bulk_add_subscriptions(
        commander.realm, [channel], people, acting_user=commander
    )
    if subscribed:
        send_user_subscribed_and_new_channel_notifications(
            user_profile=commander,
            subscribers=people,
            new_subscriptions={str(item.user.id): [channel.name] for item in subscribed},
            id_to_user_profile={str(item.user.id): item.user for item in subscribed},
            created_streams=[],
            announce=False,
        )
    mentions = " ".join(f"@**{user.full_name}|{user.id}**" for user in people)
    content = _("{mentions} — you are invited to this topic by {inviter}.").format(
        mentions=mentions, inviter=silent_mention_syntax_for_user(commander)
    )
    client = get_client("Grow Agent")
    message = check_message(
        profile.bot_user,
        client,
        Addressee.for_stream_id(channel.id, tool_input.topic),
        content,
        realm=commander.realm,
    )
    message_id = do_send_messages([message])[0].message_id
    new_names = sorted(item.user.full_name for item in subscribed)
    summary = _(
        "Subscribed {new} to #{channel} and mentioned {count} person(s) in the topic."
    ).format(
        new=", ".join(new_names) if new_names else _("no one new"),
        channel=channel.name,
        count=len(people),
    )
    return summary, {
        "channel_id": channel.id,
        "message_id": message_id,
        "user_ids": sorted(user.id for user in people),
    }


def _topic_resolve(
    commander: UserProfile, profile: "agents.AgentProfile", tool_input: p.TopicResolveInput
) -> tuple[str, dict[str, object]]:
    channel, _sub = access_stream_by_id(commander, tool_input.channel_id)
    message_id = _latest_message_id_in_topic(channel, tool_input.topic)
    bare_topic = tool_input.topic.removeprefix(RESOLVED_TOPIC_PREFIX)
    target_topic = f"{RESOLVED_TOPIC_PREFIX}{bare_topic}" if tool_input.resolved else bare_topic
    check_update_message(commander, message_id, None, target_topic, "change_all")
    verb = _("resolved") if tool_input.resolved else _("reopened")
    summary = _("Marked the topic {topic} in #{channel} as {verb}.").format(
        topic=bare_topic, channel=channel.name, verb=verb
    )
    return summary, {"channel_id": channel.id}


def _topic_move(
    commander: UserProfile, profile: "agents.AgentProfile", tool_input: p.TopicMoveInput
) -> tuple[str, dict[str, object]]:
    channel, _sub = access_stream_by_id(commander, tool_input.channel_id)
    message_id = _latest_message_id_in_topic(channel, tool_input.topic)
    target_channel_id = (
        tool_input.new_channel_id
        if tool_input.new_channel_id is not None and tool_input.new_channel_id != channel.id
        else None
    )
    check_update_message(
        commander, message_id, target_channel_id, tool_input.new_topic, "change_all"
    )
    if target_channel_id is not None:
        new_channel = get_stream_by_id_in_realm(target_channel_id, commander.realm)
        summary = _("Moved the topic {old} in #{channel} to #{new_channel} > {new}.").format(
            old=tool_input.topic,
            channel=channel.name,
            new_channel=new_channel.name,
            new=tool_input.new_topic,
        )
    else:
        summary = _("Renamed the topic {old} in #{channel} to {new}.").format(
            old=tool_input.topic, channel=channel.name, new=tool_input.new_topic
        )
    return summary, {"channel_id": target_channel_id or channel.id}


_EXECUTORS: dict[str, TeamToolExecutor] = {
    "team.find": _find,
    "channel.create": _channel_create,
    "channel.subscribe": _channel_subscribe,
    "channel.unsubscribe": _channel_unsubscribe,
    "group.create": _group_create,
    "group.add_members": _group_add_members,
    "group.remove_members": _group_remove_members,
    "topic.post": _topic_post,
    "topic.add_person": _topic_add_person,
    "topic.resolve": _topic_resolve,
    "topic.move": _topic_move,
}


def describe_team_tool_input(viewer: UserProfile, tool_input: p.TeamToolInput) -> str:
    """A human sentence for a proposed tool call, built from every typed
    argument (contract: an approval must let the commander review what the
    tool will actually do, not only a count or the tool's name)."""
    realm = viewer.realm
    if isinstance(tool_input, p.TeamFindInput):
        return _('Search for "{query}".').format(query=tool_input.query)
    if isinstance(tool_input, p.ChannelCreateInput):
        kind = _("private") if tool_input.is_private else _("public")
        people = _user_names(realm, tool_input.subscriber_user_ids)
        return _("Create {kind} channel {name}. Subscribers: {people}.").format(
            kind=kind, name=tool_input.name, people=people or _("none")
        )
    if isinstance(tool_input, p.ChannelSubscribeInput):
        return _("Subscribe {people} to {channel}.").format(
            people=_user_names(realm, tool_input.user_ids),
            channel=_channel_label(viewer, tool_input.channel_id),
        )
    if isinstance(tool_input, p.ChannelUnsubscribeInput):
        return _("Unsubscribe {people} from {channel}.").format(
            people=_user_names(realm, tool_input.user_ids),
            channel=_channel_label(viewer, tool_input.channel_id),
        )
    if isinstance(tool_input, p.GroupCreateInput):
        people = _user_names(realm, tool_input.member_user_ids)
        return _("Create group {name}. Members: {people}.").format(
            name=tool_input.name, people=people or _("none")
        )
    if isinstance(tool_input, p.GroupAddMembersInput):
        return _("Add {people} to group {group}.").format(
            people=_user_names(realm, tool_input.user_ids),
            group=_group_label(realm, tool_input.group_id),
        )
    if isinstance(tool_input, p.GroupRemoveMembersInput):
        return _("Remove {people} from group {group}.").format(
            people=_user_names(realm, tool_input.user_ids),
            group=_group_label(realm, tool_input.group_id),
        )
    if isinstance(tool_input, p.TopicPostInput):
        content = tool_input.content
        preview = content if len(content) <= 140 else content[:139] + "…"
        return _('Post in {channel} > {topic}: "{preview}"').format(
            channel=_channel_label(viewer, tool_input.channel_id),
            topic=tool_input.topic,
            preview=preview,
        )
    if isinstance(tool_input, p.TopicAddPersonInput):
        return _("Invite {people} to {channel} > {topic}.").format(
            people=_user_names(realm, tool_input.user_ids),
            channel=_channel_label(viewer, tool_input.channel_id),
            topic=tool_input.topic,
        )
    if isinstance(tool_input, p.TopicResolveInput):
        verb = _("Resolve") if tool_input.resolved else _("Reopen")
        return _("{verb} the topic {channel} > {topic}.").format(
            verb=verb, channel=_channel_label(viewer, tool_input.channel_id), topic=tool_input.topic
        )
    assert isinstance(tool_input, p.TopicMoveInput)
    channel_label = _channel_label(viewer, tool_input.channel_id)
    if tool_input.new_channel_id is not None and tool_input.new_channel_id != tool_input.channel_id:
        new_channel_label = _channel_label(viewer, tool_input.new_channel_id)
        return _("Move the topic {channel} > {topic} to {new_channel} > {new_topic}.").format(
            channel=channel_label,
            topic=tool_input.topic,
            new_channel=new_channel_label,
            new_topic=tool_input.new_topic,
        )
    return _("Move the topic {channel} > {topic} to {new_topic}.").format(
        channel=channel_label, topic=tool_input.topic, new_topic=tool_input.new_topic
    )


def execute_team_tool(
    commander: UserProfile, profile: "agents.AgentProfile", tool_input: p.TeamToolInput
) -> dict[str, object]:
    """Phase 2 of contract 2.6 item 3. Call this outside agent_transaction.

    Never raises: a Zulip permission failure becomes a failed receipt with a
    user-safe error string, taken from the JsonableError Zulip already shows
    its own users, never an internal code, table, or hash.
    """
    commander = UserProfile.objects.get(id=commander.id, realm_id=profile.realm_id, is_active=True)
    handler = _EXECUTORS[tool_input.tool]
    with override_language(commander.realm.default_language):
        try:
            summary, objects = handler(commander, profile, tool_input)
        except JsonableError as error:
            return {
                "tool": tool_input.tool,
                "outcome": "failed",
                "summary": _sanitize_receipt_text(describe_team_tool_input(commander, tool_input)),
                "objects": {},
                "error": _sanitize_receipt_text(str(error)),
            }
        return {
            "tool": tool_input.tool,
            "outcome": "succeeded",
            "summary": _sanitize_receipt_text(summary[:2048]),
            "objects": objects,
            "error": None,
        }


def team_manage_result_lines(job: "agents.AgentJob") -> list[str]:
    """The executed-steps list for a manage job's final reply (contract 2.6 item 7).

    Built only from stored receipts, so the reply never claims a step that
    did not happen.
    """
    operations = agents.AgentOperation.objects.filter(
        attempt__job=job, tool_class="team.manage", server_receipt__isnull=False
    ).order_by("created_at")
    with override_language(job.realm.default_language):
        lines = []
        for operation in operations:
            receipt = operation.server_receipt
            if receipt["outcome"] == "succeeded":
                lines.append(f"- {receipt['summary']}")
            else:
                lines.append(
                    _("- {summary} Failed: {error}").format(
                        summary=receipt["summary"], error=receipt["error"] or ""
                    )
                )
        return lines
