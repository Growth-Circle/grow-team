"""The morning reminder of the cards a member should look at today.

A member with open cards that are overdue or due today gets one direct
message from the Notification Bot while the workspace's own clock is between
07:00 and 10:00. A TaskReminderLog row marks the day as done for that member.
"""

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from django.conf import settings as django_settings
from django.db import transaction
from django.utils.translation import gettext as _
from django.utils.translation import override as override_language

from zerver.actions.message_send import internal_send_private_message
from zerver.actions.tasks import code_span, task_card_link
from zerver.lib.realm_clock import realm_local_now
from zerver.lib.sanji_format import format_date_short
from zerver.lib.tasks import readable_stream_ids, realm_task_id_prefix
from zerver.models import Realm, Task, TaskReminderLog, UserProfile
from zerver.models.users import get_system_bot

REMINDER_HOUR = 7

# A reminder that would first arrive after this hour is no longer a morning
# reminder, so the day is skipped; the cards stay on My tasks and Planner.
REMINDER_END_HOUR = 10

# A long list stops being a reminder; the rest is one click away on My tasks.
MAX_LISTED_CARDS = 10


@dataclass
class DueCard:
    task: Task
    # 0 for a card due today.
    days_overdue: int
    # The workspace's date the card is due.
    due_date: date


@dataclass
class TaskReminder:
    user: UserProfile
    cards: list[DueCard]
    # The workspace's date that this reminder is for.
    date: date


def _next_midnight(local_now: datetime) -> datetime:
    return datetime.combine(local_now.date() + timedelta(days=1), time.min, tzinfo=local_now.tzinfo)


def reminders_to_send(realm: Realm) -> list[TaskReminder]:
    """Who gets a reminder now, and for which cards. Empty before 07:00 on
    the workspace's clock, and from 10:00 on. A member already reminded
    today, a member with nothing due, and a card in a channel its assignee
    cannot read are all left out."""
    local_now = realm_local_now(realm)
    if not time(REMINDER_HOUR, 0) <= local_now.time() < time(REMINDER_END_HOUR, 0):
        return []
    today = local_now.date()

    reminded_user_ids = TaskReminderLog.objects.filter(user__realm=realm, date=today).values_list(
        "user_id", flat=True
    )
    tasks = (
        Task.objects.filter(
            realm=realm,
            completed_at__isnull=True,
            due_at__isnull=False,
            due_at__lt=_next_midnight(local_now),
            assignee__is_active=True,
            assignee__is_bot=False,
        )
        .exclude(assignee__role=UserProfile.ROLE_GUEST)
        .exclude(assignee_id__in=reminded_user_ids)
        .select_related("assignee")
        .order_by("due_at", "-priority", "id")
    )

    tasks_by_user: dict[int, list[Task]] = {}
    for task in tasks:
        assert task.assignee_id is not None
        tasks_by_user.setdefault(task.assignee_id, []).append(task)

    reminders = []
    for user_tasks in tasks_by_user.values():
        user = user_tasks[0].assignee
        assert user is not None
        readable = readable_stream_ids(
            user, {task.stream_id for task in user_tasks if task.stream_id is not None}
        )
        cards = []
        for task in user_tasks:
            if task.stream_id is not None and task.stream_id not in readable:
                continue
            assert task.due_at is not None
            due_date = task.due_at.astimezone(local_now.tzinfo).date()
            cards.append(
                DueCard(task=task, days_overdue=max(0, (today - due_date).days), due_date=due_date)
            )
        if cards:
            reminders.append(TaskReminder(user=user, cards=cards, date=today))
    return sorted(reminders, key=lambda reminder: reminder.user.id)


def reminder_content(reminder: TaskReminder) -> str:
    realm = reminder.user.realm
    prefix = realm_task_id_prefix(realm)
    with override_language(reminder.user.default_language):
        lines = [_("Good morning. These cards are due today or overdue:"), ""]
        for card in reminder.cards[:MAX_LISTED_CARDS]:
            when = (
                _("Days overdue: {days} (due {date})").format(
                    days=card.days_overdue,
                    date=format_date_short(card.due_date, reminder.user.default_language),
                )
                if card.days_overdue
                else _("Due today")
            )
            title = code_span(f"{prefix}-{card.task.counter} {card.task.title}")
            link = f"[{_('Open the card')}]({task_card_link(card.task)})"
            lines.append(f"* {title} · {when} · {link}")
        hidden = len(reminder.cards) - MAX_LISTED_CARDS
        if hidden > 0:
            lines.append(_("And {count} more.").format(count=hidden))
        open_my_tasks = _("Open My tasks")
        lines += ["", f"[{open_my_tasks}]({realm.url}/#tasks/mine/list)"]
    return "\n".join(lines)


def send_task_reminder(reminder: TaskReminder) -> bool:
    """Send the reminder unless the member already got one for its date.
    The log row is written in the same transaction as the message, so a
    failed send leaves the day open for the next run."""
    with transaction.atomic():
        _log, created = TaskReminderLog.objects.get_or_create(
            user=reminder.user, date=reminder.date
        )
        if not created:
            return False
        sender = get_system_bot(django_settings.NOTIFICATION_BOT, reminder.user.realm_id)
        internal_send_private_message(sender, reminder.user, reminder_content(reminder))
    return True
