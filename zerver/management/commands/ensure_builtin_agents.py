"""Create or archive sanji's three built-in agents: Kaki, Ayame, and Matcha.

WP13 step 7 / §4.5: each one is an `answer` profile on the runner's
endpoint harness with the given provider, described in §3.5. A rerun is
safe: it keeps each built-in, probes one that is not ready again, and
enables it when the runner reports it ready. `--archive` archives the
built-ins, and a later run without it makes new ones.
"""

import time
from argparse import ArgumentParser
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from django.core.management.base import CommandError
from django.utils.translation import gettext_lazy
from django.utils.translation import override as override_language
from django_stubs_ext import StrPromise
from typing_extensions import override

from zerver.actions.agents import (
    archive_profile,
    create_profile,
    enable_profile,
    endpoint_adapter,
    retry_profile_setup,
    share_agent_profile,
    update_team_default,
)
from zerver.actions.users import do_deactivate_user
from zerver.lib import agent_protocol as protocol
from zerver.lib.agent_context import agent_realm
from zerver.lib.management import ZulipBaseCommand
from zerver.lib.user_groups import get_role_based_system_groups_dict
from zerver.models import Realm, UserProfile, agents

# A CLI run against an offline runner should fail fast, not hang; an
# operator who needs longer reruns the command once the runner is up.
DEFAULT_READINESS_TIMEOUT_SECONDS = 30.0
READINESS_POLL_INTERVAL_SECONDS = 1.0


@dataclass(frozen=True)
class BuiltinAgent:
    name: str
    agent_role: str
    avatar_shape: str
    avatar_color: str
    description: StrPromise
    instructions: StrPromise


# §4.5 gives the role, shape, and color. §3.5 replaces the mockup text of
# Kaki and Ayame, because an `answer` profile makes no tasks, PICs, or
# pull requests. Matcha keeps the mockup text.
BUILTIN_AGENTS = [
    BuiltinAgent(
        name="Kaki",
        agent_role="planner",
        avatar_shape="circle",
        avatar_color="#FF6A3D",
        description=gettext_lazy(
            "Helps you make a plan from a brief in a direct message, and writes a "
            "channel summary every morning."
        ),
        instructions=gettext_lazy(
            "You are Kaki, the planning assistant in sanji. Help people make a plan from "
            "the brief they send you in a direct message. When someone asks, write a short "
            "channel summary every morning. Do not create tasks, do not choose a PIC, and "
            "do not open pull requests."
        ),
    ),
    BuiltinAgent(
        name="Ayame",
        agent_role="builder",
        avatar_shape="ring",
        avatar_color="#8B74FF",
        description=gettext_lazy("Writes copy, documents, and slides for review."),
        instructions=gettext_lazy(
            "You are Ayame, the writing assistant in sanji. Write copy, documents, and "
            "slides for a person to review. Do not create tasks, do not choose a PIC, and "
            "do not open pull requests."
        ),
    ),
    BuiltinAgent(
        name="Matcha",
        agent_role="reviewer",
        avatar_shape="box",
        avatar_color="#16C784",
        description=gettext_lazy(
            "Reviews the work of other agents, checks facts and numbers, then asks a "
            "person for approval."
        ),
        instructions=gettext_lazy(
            "You are Matcha, the review assistant in sanji. Review the work of other "
            "agents, and check facts and numbers. Then ask a person for approval before "
            "anything is final. Do not create tasks, do not choose a PIC, and do not open "
            "pull requests."
        ),
    ),
]


def live_builtin(realm_id: int, agent_role: str) -> agents.AgentProfile | None:
    """The built-in agent with this role that is not archived. A rename
    does not hide it, because the role identifies it."""
    return (
        agents.AgentProfile.objects.filter(
            realm_id=realm_id, is_builtin=True, agent_role=agent_role
        )
        .exclude(desired_state="archived")
        .order_by("-created_at")
        .first()
    )


def release_archived_names(realm_id: int, agent_role: str) -> None:
    """Deactivate the bot of an archived built-in, so its name is free for
    the new built-in. The archived profile and its history stay."""
    for profile in agents.AgentProfile.objects.filter(
        realm_id=realm_id,
        is_builtin=True,
        agent_role=agent_role,
        desired_state="archived",
        bot_user__is_active=True,
    ).select_related("bot_user"):
        do_deactivate_user(profile.bot_user, acting_user=None)


def create_builtin(
    owner: UserProfile,
    runner: agents.AgentRunner,
    provider: agents.AgentProvider,
    spec: BuiltinAgent,
) -> agents.AgentProfile:
    catalog = protocol.RunnerCatalog.model_validate(runner.catalog_report)
    try:
        adapter = endpoint_adapter(catalog)
    except ValueError:
        raise CommandError(
            f"Runner {runner.id} reports no ready endpoint adapter; cannot create {spec.name}."
        ) from None
    return create_profile(
        owner,
        name=spec.name,
        runner=runner,
        adapter_id=adapter.id,
        adapter_version=adapter.version,
        mode="endpoint",
        default_mode="answer",
        provider=provider,
        idempotency_key=uuid4(),
        description=str(spec.description),
        instructions=str(spec.instructions),
        agent_role=spec.agent_role,
        avatar_shape=spec.avatar_shape,
        avatar_color=spec.avatar_color,
        model_preset="balanced",
        is_builtin=True,
    )


def is_ready(profile: agents.AgentProfile) -> bool:
    return profile.readiness_state == "ready" and profile.readiness_revision == profile.revision


def wait_for_readiness(
    profile: agents.AgentProfile,
    *,
    timeout_seconds: float = DEFAULT_READINESS_TIMEOUT_SECONDS,
    poll_interval_seconds: float = READINESS_POLL_INTERVAL_SECONDS,
) -> bool:
    """Poll until the runner's probe reports this profile ready, or give up.

    A management command has no channel back from the runner other than
    the row record_setup_result() already writes; polling that row is
    the whole mechanism (there is no push from the runner to a CLI)."""
    deadline = time.monotonic() + timeout_seconds
    while True:
        current = agents.AgentProfile.objects.get(id=profile.id)
        if is_ready(current):
            return True
        if current.readiness_state not in ("unchecked", "ready") or time.monotonic() >= deadline:
            return False
        time.sleep(poll_interval_seconds)


def realm_owner_actor(realm: Realm) -> UserProfile | None:
    """The same fallback-owner convention used elsewhere in this rebrand
    (WP14 step 1): the realm Owner with the smallest id."""
    return (
        UserProfile.objects.filter(realm=realm, role=UserProfile.ROLE_REALM_OWNER, is_active=True)
        .order_by("id")
        .first()
    )


class Command(ZulipBaseCommand):
    help = "Create or archive sanji's built-in agents: Kaki, Ayame, and Matcha."

    @override
    def add_arguments(self, parser: ArgumentParser) -> None:
        self.add_realm_args(parser, required=True)
        parser.add_argument("--runner", help="Existing AgentRunner id to create the agents on.")
        parser.add_argument("--provider", help="Existing AgentProvider id to create the agents on.")
        parser.add_argument(
            "--owner", type=int, help="Owning user id (default: the runner's owner)."
        )
        parser.add_argument(
            "--share-group",
            help='A role group name (e.g. "role:members") to share the agents with.',
        )
        parser.add_argument(
            "--set-default", action="store_true", help="Make Kaki the workspace's team default."
        )
        parser.add_argument(
            "--archive", action="store_true", help="Archive the built-in agents instead."
        )
        parser.add_argument(
            "--timeout",
            type=float,
            default=DEFAULT_READINESS_TIMEOUT_SECONDS,
            help="Seconds to wait for the runner's readiness probe per agent.",
        )

    @override
    def handle(self, *args: Any, **options: Any) -> None:
        realm = self.get_realm(options)
        assert realm is not None
        with agent_realm(realm.id):
            if options["archive"]:
                self._archive(realm)
                return
            if not options["runner"] or not options["provider"]:
                raise CommandError("--runner and --provider are required unless --archive.")
            self._ensure(realm, options)

    def _archive(self, realm: Realm) -> None:
        for spec in BUILTIN_AGENTS:
            profile = live_builtin(realm.id, spec.agent_role)
            if profile is None:
                self.stdout.write(f"{spec.name}: already archived.")
                continue
            archive_profile(profile.owner, profile)
            self.stdout.write(f"{spec.name}: archived.")

    def _ensure(self, realm: Realm, options: dict[str, Any]) -> None:
        runner = agents.AgentRunner.objects.get(id=options["runner"], realm=realm)
        provider = agents.AgentProvider.objects.get(id=options["provider"], realm=realm)
        owner = (
            UserProfile.objects.get(id=options["owner"], realm=realm)
            if options["owner"] is not None
            else runner.owner
        )
        share_group_id = None
        if options["share_group"]:
            group = get_role_based_system_groups_dict(realm).get(options["share_group"])
            if group is None:
                raise CommandError(f"Unknown role group: {options['share_group']}")
            share_group_id = group.id

        kaki_profile: agents.AgentProfile | None = None
        # Built-in text uses the workspace language, not the operator's.
        with override_language(realm.default_language):
            for spec in BUILTIN_AGENTS:
                profile = live_builtin(realm.id, spec.agent_role)
                if profile is None:
                    release_archived_names(realm.id, spec.agent_role)
                    profile = create_builtin(owner, runner, provider, spec)
                    self.stdout.write(f"{spec.name}: created (id={profile.id}).")
                else:
                    self.stdout.write(f"{spec.name}: already exists (id={profile.id}).")
                    if not is_ready(profile):
                        # An earlier probe grant expires after 10 minutes, so
                        # an agent made while the runner was offline needs a
                        # new probe.
                        retry_profile_setup(
                            profile.owner,
                            profile,
                            expected_revision=profile.revision,
                            retry_key=uuid4(),
                        )
                if share_group_id is not None:
                    # Every run, so a grant revoked since the last run returns.
                    share_agent_profile(profile.owner, profile, principal_group_id=share_group_id)

                if profile.desired_state == "enabled":
                    self.stdout.write(f"{spec.name}: already enabled.")
                elif wait_for_readiness(profile, timeout_seconds=options["timeout"]):
                    profile = agents.AgentProfile.objects.get(id=profile.id)
                    enable_profile(profile.owner, profile, expected_revision=profile.revision)
                    self.stdout.write(f"{spec.name}: enabled.")
                else:
                    self.stdout.write(
                        self.style.WARNING(f"{spec.name}: readiness failed; not enabled.")
                    )

                if spec.agent_role == "planner":
                    kaki_profile = profile

        if options["set_default"] and kaki_profile is not None:
            actor = realm_owner_actor(realm)
            if actor is None:
                self.stdout.write(self.style.WARNING("--set-default: no active Owner found."))
            else:
                settings = agents.AgentRealmSettings.objects.get(realm=realm)
                update_team_default(
                    actor,
                    profile=kaki_profile,
                    expected_selection_revision=settings.default_selection_revision,
                )
                self.stdout.write("Kaki: set as team default.")
