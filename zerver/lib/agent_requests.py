"""Strict human connection payloads. Resource IDs never confer authority."""

from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import AwareDatetime, Field, field_validator, model_validator

from zerver.lib import agent_protocol as p


class Request(p.Versioned):
    schema_version: Literal[1]


class PairingApproval(Request):
    pairing_id: UUID
    user_code: p.Text


ProviderStoredText = Annotated[str, Field(min_length=1, max_length=200)]
ProviderURL = Annotated[str, Field(min_length=1, max_length=2048)]
ProviderTokenCount = Annotated[int, Field(strict=True, ge=1, le=2147483647)]


class ProviderCreate(Request):
    runner_id: UUID
    name: ProviderStoredText
    base_url: ProviderURL
    model_id: ProviderStoredText
    allowed_models: list[p.Text] = Field(min_length=1, max_length=100)
    context_window_tokens: ProviderTokenCount
    max_output_tokens: ProviderTokenCount
    credential: p.Text | None = None
    local_credential_ref: Annotated[str, Field(max_length=200)] = ""
    api_mode: Literal["chat_completions", "responses"] = "chat_completions"
    network: p.NetworkPolicy = Field(default_factory=p.NetworkPolicy)
    data_scope: list[Literal["synthetic", "selected_chat", "selected_repository"]] = Field(
        default=["synthetic"]
    )


class RepositoryCreate(Request):
    runner_id: UUID
    workspace_alias: Annotated[str, Field(pattern=r"^[a-zA-Z0-9_-]{1,80}$")]
    canonical_origin: str | None = None
    allowed_refs: list[p.Text] = Field(min_length=1, max_length=100)
    required_checks: list[p.RequiredCheck] = Field(default_factory=list, max_length=64)


class ProfileCreate(Request):
    runner_id: UUID
    name: p.Text
    description: Annotated[str, Field(max_length=2000)] = ""
    adapter_id: Annotated[str, Field(min_length=1, max_length=80)]
    adapter_version: Annotated[str, Field(min_length=1, max_length=100)]
    mode: Literal["acp", "endpoint"] = "acp"
    default_mode: p.JobKind = "answer"
    idempotency_key: UUID
    provider_id: UUID | None = None
    provider_network_version: p.Positive | None = None
    repository_id: UUID | None = None
    sandbox_alias: p.Text = "default"
    actions: list[p.ExecutionAction] = Field(default=["context.read"], max_length=32)
    # The no-provider default from contract 3.5. A selected provider's own limits
    # scale this further in the action layer, which sees the provider row.
    budget: p.Budget = Field(
        default_factory=lambda: p.Budget(input_tokens=400000, output_tokens=16000)
    )
    network: p.NetworkPolicy = Field(default_factory=p.NetworkPolicy)
    hard_cost_cap: bool = Field(default=False, strict=True)

    @model_validator(mode="after")
    def valid_network_choice(self) -> Self:
        if self.provider_network_version is not None and (
            self.provider_id is None or "network" in self.model_fields_set
        ):
            raise ValueError("Choose a provider snapshot or an explicit network.")
        return self


class RevisionRequest(Request):
    expected_revision: p.Positive


class RunnerMetadataUpdate(Request):
    expected_metadata_revision: p.Positive
    name: Annotated[str, Field(min_length=1, max_length=200)]
    host_kind: Literal["workstation", "server", "unknown"]

    @field_validator("name")
    @classmethod
    def safe_name(cls, value: str) -> str:
        if any(char in value for char in "/\\") or any(
            ord(char) < 32 or ord(char) == 127 for char in value
        ):
            raise ValueError("Runner name is invalid.")
        return value


class ProviderUpdate(Request):
    expected_config_version: p.Positive
    expected_metadata_revision: p.Positive
    name: ProviderStoredText
    base_url: ProviderURL
    model_id: ProviderStoredText
    allowed_models: list[p.Text] = Field(min_length=1, max_length=100)
    context_window_tokens: ProviderTokenCount
    max_output_tokens: ProviderTokenCount
    credential_replacement: p.Text | None = None
    local_credential_ref: Annotated[str, Field(max_length=200)] | None = None
    api_mode: Literal["chat_completions", "responses"] = "chat_completions"
    network: p.NetworkPolicy = Field(default_factory=p.NetworkPolicy)
    data_scope: list[Literal["synthetic", "selected_chat", "selected_repository"]] = Field(
        default=["synthetic"]
    )


class ProfileUpdate(Request):
    expected_metadata_revision: p.Positive
    expected_revision: p.Positive
    name: p.Text
    description: Annotated[str, Field(max_length=2000)] = ""
    adapter_id: Annotated[str, Field(min_length=1, max_length=80)]
    adapter_version: Annotated[str, Field(min_length=1, max_length=100)]
    mode: Literal["acp", "endpoint"] = "acp"
    default_mode: p.JobKind = "answer"
    provider_id: UUID | None = None
    provider_network_version: p.Positive | None = None
    repository_id: UUID | None = None
    sandbox_alias: p.Text = "default"
    actions: list[p.ExecutionAction] = Field(default=["context.read"], max_length=32)
    budget: p.Budget = Field(
        default_factory=lambda: p.Budget(input_tokens=400000, output_tokens=16000)
    )
    network: p.NetworkPolicy | None = None
    retain_network: bool = Field(default=False, strict=True)
    hard_cost_cap: bool = Field(default=False, strict=True)

    @model_validator(mode="after")
    def valid_network_choice(self) -> Self:
        if self.provider_network_version is not None and (
            self.provider_id is None
            or "network" in self.model_fields_set
            or "retain_network" in self.model_fields_set
        ):
            raise ValueError("Choose a provider snapshot or another network choice.")
        if self.retain_network and self.network is not None:
            raise ValueError("Choose retained or explicit network configuration.")
        return self


class ArchiveProfile(Request):
    expected_revision: p.Positive


class DefaultSelectionUpdate(Request):
    expected_selection_revision: p.Positive
    profile_id: UUID | None = None


class SelectionResolve(Request):
    destination: p.ConversationScope | None = None
    source_message_id: p.Positive | None = None
    job_kind: p.JobKind | None = None
    repository_id: UUID | None = None
    explicit_profile_id: UUID | None = None
    selection_state: Literal["unset", "explicit", "cleared"] = "unset"

    @model_validator(mode="after")
    def valid_selection(self) -> Self:
        if (self.destination is None) == (self.source_message_id is None):
            raise ValueError("Provide one task context.")
        if self.destination is not None and (
            self.destination.kind == "selected" or self.destination.anchor_message_id is not None
        ):
            raise ValueError("Destination must not contain a source message.")
        if (self.selection_state == "explicit") != (self.explicit_profile_id is not None):
            raise ValueError("Explicit selection requires a profile.")
        return self


class SetupRequest(RevisionRequest):
    retry_key: UUID


class ChannelRequest(RevisionRequest):
    stream_id: p.Positive


class ProfilePrincipal(Request):
    principal_user_id: p.Positive | None = None
    principal_group_id: p.Positive | None = None

    @model_validator(mode="after")
    def exactly_one_principal(self) -> Self:
        if (self.principal_user_id is None) == (self.principal_group_id is None):
            raise ValueError("Choose exactly one share principal.")
        return self


class ProfileShare(ProfilePrincipal):
    allow_job_control: bool = Field(default=False, strict=True)
    allow_job_review: bool = Field(default=False, strict=True)


class ProfileUnshare(ProfilePrincipal):
    pass


class GrantCreate(Request):
    target_kind: Literal["runner", "provider", "repository", "profile"]
    target_id: UUID
    expected_revision: p.Positive
    actions: list[p.Action] = Field(min_length=1, max_length=32)
    principal_user_id: p.Positive | None = None
    principal_group_id: p.Positive | None = None
    scope: p.ConversationScope | None = None
    repository_id: UUID | None = None
    expires_at: AwareDatetime | None = None


class SetupClaim(Request):
    setup_id: UUID
    claim_key: UUID


class SetupResult(Request):
    setup_id: UUID
    claim_key: UUID
    lease_epoch: p.Positive
    descriptor_digest: p.Digest
    configuration_digest: p.Digest
    state: Literal["ready", "needs_action", "failed"]
    capabilities: p.CapabilityReport
    requirements: list[p.Requirement] = Field(default_factory=list, max_length=32)


class WorkspaceReport(Request):
    workspace_alias: Annotated[str, Field(pattern=r"^[a-zA-Z0-9_-]{1,80}$")]
    canonical_origin: str | None = None
    allowed_refs: list[p.Text] = Field(min_length=1, max_length=100)
    required_checks: list[p.RequiredCheck] = Field(default_factory=list, max_length=64)
    revision: p.Positive
