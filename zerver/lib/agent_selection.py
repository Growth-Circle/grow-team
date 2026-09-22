"""Advisory profile selection for a new task form."""

from dataclasses import asdict, dataclass
from uuid import UUID

from zerver.actions.agent_dispatch import preflight_profile
from zerver.lib import agent_protocol as p
from zerver.lib.agent_policy import AgentAccessDenied, _readable_scope, accessible_profiles
from zerver.lib.agent_presence import observed_runner_status
from zerver.lib.exceptions import JsonableError
from zerver.lib.message import access_message
from zerver.models import Message, UserProfile, agents


@dataclass(frozen=True)
class SelectionResolution:
    selection_source: str
    profile_id: str | None
    profile_revision: int | None
    selection_revision: int | None
    selection_state: str
    job_kind: str | None
    repository_id: str | None
    runner: dict[str, str] | None
    eligible: bool
    queue_permitted: bool
    reason: str

    def data(self) -> dict[str, object]:
        return asdict(self)


def resolve_agent_selection(
    actor: UserProfile,
    *,
    destination: p.ConversationScope | None,
    source_message_id: int | None,
    job_kind: str | None,
    repository_id: UUID | None,
    explicit_profile_id: UUID | None,
    selection_state: str,
) -> SelectionResolution:
    if (destination is None) == (source_message_id is None):
        raise ValueError("Provide one task context.")
    if destination is not None:
        readable, _scope = _readable_scope(actor, actor, p.serialize_payload(destination))
        if not readable:
            raise ValueError("Task context is unavailable.")
    source: Message | None = None
    if source_message_id is not None:
        try:
            source = access_message(actor, source_message_id, is_modifying_message=False)
        except JsonableError:
            raise ValueError("Task context is unavailable.") from None
        if source.realm_id != actor.realm_id:
            raise ValueError("Task context is unavailable.")

    def empty(reason: str, state: str = "none") -> SelectionResolution:
        return SelectionResolution(
            state, None, None, None, selection_state, None, None, None, False, False, reason
        )

    if selection_state == "cleared":
        return empty("cleared")

    selection_revision = None
    if explicit_profile_id is not None:
        candidate_id = explicit_profile_id
        selection_source = "explicit"
    else:
        settings = agents.AgentRealmSettings.objects.filter(realm=actor.realm).first()
        if settings is None or settings.default_profile_id is None:
            return empty("no_eligible_default")
        candidate_id = settings.default_profile_id
        selection_revision = settings.default_selection_revision
        selection_source = "team_default"

    profile = (
        accessible_profiles(actor)
        .select_related("runner", "default_repository")
        .filter(id=candidate_id)
        .first()
    )
    if profile is None:
        return empty("no_eligible_default" if selection_source == "team_default" else "unavailable")

    effective_kind = job_kind or profile.default_mode
    repository = None
    if repository_id is not None:
        repository = agents.AgentRepository.objects.filter(
            id=repository_id, realm=actor.realm
        ).first()
    elif effective_kind == "code":
        repository = profile.default_repository
    observed_status = observed_runner_status(profile.runner)
    runner = {
        "name": profile.runner.name,
        "host_kind": profile.runner.host_kind,
        "status": observed_status,
    }

    def result(reason: str, eligible: bool = False) -> SelectionResolution:
        return SelectionResolution(
            selection_source=selection_source,
            profile_id=str(profile.id),
            profile_revision=profile.revision,
            selection_revision=selection_revision,
            selection_state=selection_state,
            job_kind=effective_kind,
            repository_id=str(repository.id) if repository is not None else None,
            runner=runner,
            eligible=eligible,
            queue_permitted=eligible,
            reason=reason,
        )

    if repository_id is not None and repository is None:
        return result("repository_unavailable")
    if repository is not None and repository.id != profile.default_repository_id:
        return result("repository_unavailable")
    if profile.desired_state != "enabled" or profile.archived_at is not None:
        return result("profile_unavailable")
    if observed_status == "revoked":
        return result("runner_unavailable")
    if effective_kind == "code" and not profile.capability_report.get("code_ready", False):
        return result("coding_unavailable")
    try:
        decision = preflight_profile(
            actor,
            profile,
            source,
            destination,
            job_kind=effective_kind,
            repository=repository,
            strict_readiness=True,
        )
    except AgentAccessDenied:
        return result("access_denied")
    except (ValueError, agents.AgentRealmSettings.DoesNotExist) as error:
        if str(error) == "Agent queue is full.":
            return result("queue_full")
        return result("profile_not_ready")
    if decision == "needs_input":
        return result("coding_needs_input")
    if observed_status in {"offline", "unknown"}:
        return result(f"runner_{observed_status}", True)
    return result("available", True)
