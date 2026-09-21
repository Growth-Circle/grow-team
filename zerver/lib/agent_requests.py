"""Strict human connection payloads. Resource IDs never confer authority."""

from typing import Annotated, Literal
from uuid import UUID

from pydantic import AwareDatetime, Field

from zerver.lib import agent_protocol as p


class Request(p.Versioned):
    schema_version: Literal[1]


class PairingApproval(Request):
    pairing_id: UUID
    user_code: p.Text


class ProviderCreate(Request):
    runner_id: UUID
    name: p.Text
    base_url: p.Text
    model_id: p.Text
    allowed_models: list[p.Text] = Field(min_length=1, max_length=100)
    context_window_tokens: p.Positive
    max_output_tokens: p.Positive
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
    adapter_id: p.Text
    adapter_version: p.Text
    mode: Literal["acp", "endpoint"] = "acp"
    default_mode: Literal["answer", "code"] = "answer"
    idempotency_key: UUID
    provider_id: UUID | None = None
    repository_id: UUID | None = None
    sandbox_alias: p.Text = "default"
    actions: list[p.ExecutionAction] = Field(default=["context.read"], max_length=32)
    budget: p.Budget = Field(default_factory=lambda: p.Budget(input_tokens=1024, output_tokens=512))
    network: p.NetworkPolicy = Field(default_factory=p.NetworkPolicy)
    hard_cost_cap: bool = Field(default=False, strict=True)


class RevisionRequest(Request):
    expected_revision: p.Positive


class SetupRequest(RevisionRequest):
    retry_key: UUID


class ChannelRequest(RevisionRequest):
    stream_id: p.Positive


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
