"""Version 1 agent DTOs. These schemas validate data, not runtime authority."""

import hashlib
import json
from typing import Annotated, Literal
from urllib.parse import urlsplit
from uuid import UUID

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    TypeAdapter,
    field_validator,
    model_validator,
)
from typing_extensions import Self

SCHEMA_VERSION = 1
MAX_EVENT_BYTES = 64 * 1024
MAX_ARTIFACT_BYTES = 10 * 1024 * 1024
MAX_JOB_ARTIFACT_BYTES = 50 * 1024 * 1024

Positive = Annotated[int, Field(strict=True, ge=1)]
Nonnegative = Annotated[int, Field(strict=True, ge=0)]
Text = Annotated[str, Field(min_length=1, max_length=4096)]
Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
GitHash = Annotated[str, Field(pattern=r"^[0-9a-f]{40}([0-9a-f]{24})?$")]
Action = Literal[
    "context.read",
    "repository.read",
    "repository.edit",
    "checks.run",
    "shell.run",
    "dependencies.install",
    "git.commit",
    "git.push",
    "git.draft_pr",
    "job.control",
    "job.review",
    "profile.use",
    "provider.use",
    "runner.use",
    "profile.manage",
    "team.manage",
]
ExecutionAction = Literal[
    "context.read",
    "repository.read",
    "repository.edit",
    "checks.run",
    "shell.run",
    "dependencies.install",
    "git.commit",
    "git.push",
    "git.draft_pr",
]
JobState = Literal[
    "draft",
    "queued",
    "running",
    "waiting_for_input",
    "waiting_for_approval",
    "blocked",
    "verifying",
    "cancel_requested",
    "cancelled",
    "interrupted",
    "failed",
    "completed",
]
JobKind = Literal["answer", "code", "manage"]
TeamToolId = Literal[
    "team.find",
    "channel.create",
    "channel.subscribe",
    "channel.unsubscribe",
    "group.create",
    "group.add_members",
    "group.remove_members",
    "topic.post",
    "topic.add_person",
    "topic.resolve",
    "topic.move",
]
DeliveryTarget = Literal["answer", "patch", "draft_pr"]
ProfileState = Literal["draft", "enabled", "paused", "archived"]
RunnerState = Literal["online", "offline", "unknown", "revoked"]
ReadinessState = Literal["unchecked", "checking", "ready", "needs_action", "error"]
ProcessState = Literal["starting", "active", "stopping", "stopped", "unknown"]
Phase = Literal["inspect", "plan", "edit", "verify", "review", "deliver"]
CapabilityResult = Literal["passed", "unsupported", "unknown"]
InputState = Literal["pending", "delivered", "applied", "delivery_uncertain", "cancelled"]
TriggerKind = Literal["mention", "direct_message", "manual"]
OperationState = Literal[
    "proposed", "authorized", "started", "succeeded", "failed", "outcome_unknown", "cancelled"
]
ApprovalState = Literal["pending", "approved", "rejected", "expired", "cancelled", "consumed"]


class Record(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_default=True)


class Versioned(Record):
    schema_version: Literal[1] = 1

    @field_validator("schema_version", mode="before")
    @classmethod
    def exact_integer_version(cls, value: object) -> object:
        if type(value) is not int:
            raise ValueError("Schema version must be an integer")
        return value


class CapabilityReport(Record):
    chat_ready: StrictBool = False
    code_ready: StrictBool = False
    tool_calling: CapabilityResult = "unknown"
    streaming: CapabilityResult = "unknown"
    usage: CapabilityResult = "unknown"
    native_resume: CapabilityResult = "unknown"
    steering: CapabilityResult = "unknown"
    sandbox: CapabilityResult = "unknown"
    probed_at: AwareDatetime | None = None
    runner_version: str = ""
    adapter_version: str = ""
    config_version: Positive = 1

    @model_validator(mode="after")
    def code_requires_evidence(self) -> Self:
        if self.code_ready and (
            not self.chat_ready or self.tool_calling != "passed" or self.sandbox != "passed"
        ):
            raise ValueError("Coding requires chat, tools, and sandbox evidence")
        return self


class Requirement(Record):
    code: Annotated[str, Field(pattern=r"^[a-z0-9_]{1,80}$")]
    surface: Literal[
        "runner", "runner_workspace", "adapter", "provider", "grant", "sandbox", "diagnostic"
    ]
    action: Literal[
        "connect_runner",
        "register_workspace",
        "install_adapter",
        "login_vendor",
        "edit_provider",
        "probe_again",
        "request_grant",
        "configure_sandbox",
        "view_diagnostic",
    ]
    diagnostic_id: UUID | None = None


class ReadinessReport(Versioned):
    profile_id: UUID
    profile_revision: Positive
    runner_id: UUID
    descriptor_digest: Digest
    configuration_digest: Digest
    state: ReadinessState
    capabilities: CapabilityReport
    requirements: list[Requirement] = Field(default_factory=list, max_length=32)


class NetworkTarget(Record):
    hostname: Annotated[str, Field(min_length=1, max_length=253, pattern=r"^[a-zA-Z0-9.:-]+$")]
    port: Annotated[int, Field(strict=True, ge=1, le=65535)]
    allow_private: StrictBool = False
    allow_http_loopback: StrictBool = False
    allow_http_private: StrictBool = False

    @model_validator(mode="after")
    def private_http_requires_private_scope(self) -> Self:
        if self.allow_http_private and not self.allow_private:
            raise ValueError("Private HTTP requires private endpoint approval")
        return self


class NetworkPolicy(Record):
    targets: list[NetworkTarget] = Field(default_factory=list, max_length=32)
    public_https_only: Literal[True] = True
    block_metadata: Literal[True] = True
    cross_origin_authorization: Literal[False] = False
    project_network: Literal[False] = False


class SecretReference(Record):
    kind: Literal["server", "local"]
    id: Text
    version: Positive


class ProviderConfig(Record):
    id: UUID
    owner_user_id: Positive
    runner_id: UUID
    name: Text
    base_url: Annotated[str, Field(pattern=r"^https?://[^\s]+$", max_length=2048)]
    api_mode: Literal["chat_completions", "responses"]
    model_id: Text
    allowed_models: list[Text] = Field(min_length=1, max_length=100)
    credential_ref: SecretReference | None = None
    context_window_tokens: Positive
    max_output_tokens: Positive
    config_version: Positive
    data_scope: list[Literal["synthetic", "selected_chat", "selected_repository"]] = Field(
        min_length=1
    )
    network: NetworkPolicy
    capability_report: CapabilityReport

    @model_validator(mode="after")
    def validate_model(self) -> Self:
        url = urlsplit(self.base_url)
        if not url.hostname or url.username or url.password or url.query or url.fragment:
            raise ValueError("Provider URL must not contain credentials, query, or fragment")
        if url.port is not None and not 1 <= url.port <= 65535:
            raise ValueError("Invalid provider port")
        if self.model_id not in self.allowed_models:
            raise ValueError("Model is not allowed")
        if self.max_output_tokens > self.context_window_tokens:
            raise ValueError("Output budget exceeds context window")
        if self.capability_report.config_version != self.config_version:
            raise ValueError("Capability report is stale")
        return self


class RequiredCheck(Record):
    id: Annotated[str, Field(pattern=r"^[a-zA-Z0-9_-]{1,80}$")]
    argv: list[Text] = Field(min_length=1, max_length=64)
    cwd: Annotated[str, Field(max_length=512)] = "."
    timeout_seconds: Annotated[int, Field(strict=True, ge=1, le=1200)] = 120

    @model_validator(mode="after")
    def relative_workspace(self) -> Self:
        if (
            self.cwd.startswith(("/", "\\"))
            or ".." in self.cwd.replace("\\", "/").split("/")
            or ":" in self.cwd
        ):
            raise ValueError("Check directory must stay in the workspace")
        return self


class RepositoryConfig(Record):
    id: UUID
    owner_user_id: Positive
    runner_id: UUID
    workspace_alias: Annotated[str, Field(pattern=r"^[a-zA-Z0-9_-]{1,80}$")]
    canonical_origin: str | None = None
    allowed_refs: list[Text] = Field(min_length=1, max_length=100)
    base_ref: Text
    base_commit: GitHash | None
    policy_version: Positive
    required_checks: list[RequiredCheck] = Field(default_factory=list, max_length=64)

    @model_validator(mode="after")
    def allowed_base(self) -> Self:
        if self.base_ref not in self.allowed_refs:
            raise ValueError("Base ref is not allowed")
        return self


class ConversationScope(Record):
    kind: Literal["stream", "direct", "selected"]
    stream_id: Positive | None = None
    topic: Annotated[str, Field(max_length=200)] | None = None
    participant_user_ids: list[Positive] = Field(default_factory=list, max_length=100)
    anchor_message_id: Positive | None = None

    @model_validator(mode="after")
    def consistent_scope(self) -> Self:
        if self.kind == "stream" and (self.stream_id is None or self.participant_user_ids):
            raise ValueError("Stream scope requires a stream and no DM participants")
        if self.kind == "direct" and (self.stream_id is not None or not self.participant_user_ids):
            raise ValueError("Direct scope requires participants and no stream")
        if self.kind == "selected" and self.anchor_message_id is None:
            raise ValueError("Selected scope requires an anchor")
        return self


class AudienceBinding(Record):
    conversation_id: UUID
    epoch: Positive
    realm_id: Positive
    profile_id: UUID
    requester_user_id: Positive
    bot_user_id: Positive
    anchor_message_id: Positive
    recipient_id: Positive
    kind: Literal["stream", "direct"]
    stream_id: Positive | None = None
    invite_only: StrictBool | None = None
    is_web_public: StrictBool | None = None
    history_public_to_subscribers: StrictBool | None = None
    audience_user_ids: list[Positive] = Field(min_length=1, max_length=10000)

    @model_validator(mode="after")
    def matching_audience(self) -> Self:
        if (self.kind == "stream") != (self.stream_id is not None):
            raise ValueError("Audience route does not match its kind")
        visibility = [self.invite_only, self.is_web_public, self.history_public_to_subscribers]
        if (self.kind == "stream" and any(value is None for value in visibility)) or (
            self.kind == "direct" and any(value is not None for value in visibility)
        ):
            raise ValueError("Audience visibility does not match its route")
        if self.audience_user_ids != sorted(set(self.audience_user_ids)):
            raise ValueError("Audience members must be unique and sorted")
        if not {self.requester_user_id, self.bot_user_id} <= set(self.audience_user_ids):
            raise ValueError("Audience must include both principals")
        return self


class GrantConfig(Record):
    id: UUID
    principal_user_id: Positive | None = None
    principal_group_id: Positive | None = None
    target_kind: Literal["runner", "provider", "repository", "profile"] = "profile"
    runner_id: UUID | None = None
    provider_id: UUID | None = None
    profile_id: UUID | None = None
    repository_id: UUID | None = None
    scope: ConversationScope | None = None
    actions: list[Action] = Field(min_length=1)
    policy_version: Positive
    expires_at: AwareDatetime | None = None

    @model_validator(mode="after")
    def constrain_grant(self) -> Self:
        if (self.principal_user_id is None) == (self.principal_group_id is None):
            raise ValueError("Grant requires exactly one principal")
        targets = {
            "runner": self.runner_id,
            "provider": self.provider_id,
            "repository": self.repository_id,
            "profile": self.profile_id,
        }
        if targets[self.target_kind] is None:
            raise ValueError("Grant target is missing")
        allowed = {self.target_kind} | ({"repository"} if self.target_kind == "profile" else set())
        if any(value is not None and name not in allowed for name, value in targets.items()):
            raise ValueError("Grant targets do not match their kind")
        actions = {
            "runner": {"runner.use"},
            "provider": {"provider.use"},
            "repository": {
                "repository.read",
                "repository.edit",
                "checks.run",
                "shell.run",
                "dependencies.install",
                "git.commit",
                "git.push",
                "git.draft_pr",
            },
        }
        if self.target_kind in actions and set(self.actions) - actions[self.target_kind]:
            raise ValueError("Action does not match grant target")
        return self


class AdapterConfig(Record):
    id: Annotated[str, Field(pattern=r"^[a-zA-Z0-9_-]{1,80}$")]
    version: Text
    mode: Literal["acp", "endpoint"]


class SandboxConfig(Record):
    kind: Literal["rootless_container"] = "rootless_container"
    alias: Annotated[str, Field(pattern=r"^[a-zA-Z0-9_-]{1,80}$")]
    image_digest: Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")]
    toolchain_digest: Digest
    catalog_revision: Positive
    cpu_millicores: Annotated[int, Field(strict=True, ge=100, le=64000)]
    memory_bytes: Annotated[int, Field(strict=True, ge=67108864, le=137438953472)]
    pids_limit: Annotated[int, Field(strict=True, ge=16, le=4096)]
    temporary_bytes: Annotated[int, Field(strict=True, ge=1048576, le=10737418240)]
    network_mode: Literal["none"] = "none"
    read_only_root: Literal[True] = True
    drop_all_capabilities: Literal[True] = True


class AdapterCatalogEntry(Record):
    id: Annotated[str, Field(pattern=r"^[a-zA-Z0-9_-]{1,80}$")]
    version: Text
    auth_state: Literal["unchecked", "ready", "login_required", "expired", "error"]
    capabilities: CapabilityReport


class RunnerCatalog(Record):
    revision: Positive
    reported_at: AwareDatetime | None = None
    adapters: list[AdapterCatalogEntry] = Field(default_factory=list, max_length=100)
    sandboxes: list[SandboxConfig] = Field(default_factory=list, max_length=100)


class Policy(Record):
    version: Positive
    actions: list[ExecutionAction]
    grant_ids: list[UUID] = Field(default_factory=list, max_length=100)
    scope: ConversationScope
    sandbox: SandboxConfig
    network: NetworkPolicy
    hard_cost_cap: StrictBool = False


class Budget(Record):
    active_seconds: Annotated[int, Field(strict=True, ge=1, le=7200)] = 3600
    tool_rounds: Annotated[int, Field(strict=True, ge=1, le=40)] = 40
    shell_timeout_seconds: Annotated[int, Field(strict=True, ge=1, le=120)] = 120
    transport_retries: Annotated[int, Field(strict=True, ge=0, le=2)] = 2
    context_recoveries: Annotated[int, Field(strict=True, ge=0, le=2)] = 2
    input_tokens: Positive
    output_tokens: Positive
    tool_output_bytes: Annotated[int, Field(strict=True, ge=1, le=51200)] = 51200
    artifact_bytes: Annotated[int, Field(strict=True, ge=1, le=MAX_ARTIFACT_BYTES)] = (
        MAX_ARTIFACT_BYTES
    )
    job_artifact_bytes: Annotated[int, Field(strict=True, ge=1, le=MAX_JOB_ARTIFACT_BYTES)] = (
        MAX_JOB_ARTIFACT_BYTES
    )
    cost_limit_microunits: Positive | None = None


class ProfileConfig(Versioned):
    id: UUID
    owner_user_id: Positive
    bot_user_id: Positive
    runner_id: UUID
    name: Text
    description: Annotated[str, Field(max_length=4096)] = ""
    revision: Positive
    desired_state: ProfileState
    default_mode: JobKind
    default_repository_id: UUID | None = None
    adapter: AdapterConfig
    provider_id: UUID | None = None
    enabled_revision: Positive | None = None
    readiness_revision: Positive | None = None
    readiness_state: ReadinessState
    capabilities: CapabilityReport
    policy: Policy
    budget: Budget

    @model_validator(mode="after")
    def configured_mode(self) -> Self:
        if self.adapter.mode == "endpoint" and self.provider_id is None:
            raise ValueError("Endpoint mode requires a provider")
        if self.desired_state == "enabled" and self.enabled_revision is None:
            raise ValueError("Enabled profile requires its authorized revision")
        if any(
            revision is not None and revision > self.revision
            for revision in [self.enabled_revision, self.readiness_revision]
        ):
            raise ValueError("Profile revision cannot refer to a future configuration")
        return self


class ContextReference(Record):
    id: UUID
    kind: Literal["message", "attachment", "repository"]
    message_id: Positive | None = None
    attachment_id: Positive | None = None
    repository_id: UUID | None = None
    scope: ConversationScope
    validated_at: AwareDatetime

    @model_validator(mode="after")
    def matching_reference(self) -> Self:
        fields = {
            "message": self.message_id,
            "attachment": self.attachment_id,
            "repository": self.repository_id,
        }
        if fields[self.kind] is None or sum(value is not None for value in fields.values()) != 1:
            raise ValueError("Context requires one matching reference")
        return self


class InputRecord(Record):
    id: UUID
    author_user_id: Positive
    source_message_id: Positive | None = None
    client_key: UUID
    sequence: Positive
    input_type: Literal["steering", "answer", "replan"]
    text: Annotated[str, Field(min_length=1, max_length=20000)]
    delivery_state: InputState


class Checkpoint(Record):
    id: UUID
    source_attempt_id: UUID
    base_commit: GitHash | None = None
    tree_hash: GitHash | None = None
    summary: Annotated[str, Field(max_length=20000)]
    context_ref_ids: list[UUID] = Field(default_factory=list, max_length=100)
    artifact_ids: list[UUID] = Field(default_factory=list, max_length=100)
    remaining_work: list[Text] = Field(default_factory=list, max_length=100)
    next_step: Text
    adapter_session_ref: str | None = None
    input_cursor: Nonnegative = 0


class LeaseIdentity(Record):
    job_id: UUID
    attempt_id: UUID
    lease_epoch: Positive


class WorkspaceBinding(Record):
    canonical_origin: Text | None
    repository_id: UUID
    workspace_alias: Annotated[str, Field(pattern=r"^[a-zA-Z0-9_-]{1,80}$")]
    policy_version: Positive
    checks_digest: Digest
    allowed_refs: list[Text]


class EffectiveProvider(Record):
    id: UUID
    config_version: Positive
    base_url: str
    api_mode: Literal["chat_completions", "responses"]
    model_id: str
    allowed_models: list[str]
    credential_ref: SecretReference | None
    context_window_tokens: Positive
    max_output_tokens: Positive
    data_scope: list[str]
    network: NetworkPolicy


class ExecutionConfiguration(Versioned):
    runner_id: UUID
    profile_revision: Positive
    adapter: AdapterConfig
    provider: EffectiveProvider | None
    workspace_binding: WorkspaceBinding | None
    policy_version: Positive
    actions: list[ExecutionAction]
    sandbox: SandboxConfig
    network: NetworkPolicy
    hard_cost_cap: StrictBool
    budget: Budget


class AttemptDescriptor(LeaseIdentity, Versioned):
    audience: AudienceBinding
    tested_configuration: ExecutionConfiguration
    profile_id: UUID
    profile_revision: Positive
    descriptor_digest: Digest
    configuration_digest: Digest
    lease_expires_at: AwareDatetime
    job_kind: JobKind
    delivery_target: DeliveryTarget
    request: Annotated[str, Field(min_length=1, max_length=20000)]
    runner_id: UUID
    adapter: AdapterConfig
    provider: ProviderConfig | None
    repository: RepositoryConfig | None
    policy: Policy
    budget: Budget
    context_refs: list[ContextReference] = Field(default_factory=list, max_length=100)
    inputs: list[InputRecord] = Field(default_factory=list, max_length=100)
    checkpoint: Checkpoint | None = None

    @model_validator(mode="after")
    def constrain_execution(self) -> Self:
        if (
            self.audience.profile_id != self.profile_id
            or (
                self.policy.scope.anchor_message_id is not None
                and self.policy.scope.anchor_message_id != self.audience.anchor_message_id
            )
            or (
                self.policy.scope.kind == "stream"
                and self.policy.scope.stream_id != self.audience.stream_id
            )
            or (
                self.policy.scope.kind == "direct"
                and sorted(self.policy.scope.participant_user_ids)
                != self.audience.audience_user_ids
            )
        ):
            raise ValueError("Attempt audience does not match its scope")
        if self.job_kind == "answer":
            if self.delivery_target != "answer" or set(self.policy.actions) - {
                "context.read",
                "repository.read",
            }:
                raise ValueError("Answer jobs cannot carry mutation authority")
        elif self.job_kind == "manage":
            if (
                self.delivery_target != "answer"
                or self.repository is not None
                or set(self.policy.actions) - {"context.read"}
            ):
                raise ValueError("Manage jobs cannot carry a repository or code authority")
        elif self.delivery_target == "answer" or self.repository is None:
            raise ValueError("Code jobs require a repository and code delivery target")
        if self.adapter.mode == "endpoint" and self.provider is None:
            raise ValueError("Endpoint descriptor requires a provider")
        if any(
            item is not None and item.runner_id != self.runner_id
            for item in [self.provider, self.repository]
        ):
            raise ValueError("Descriptor references a different runner")
        if self.policy.hard_cost_cap and (
            self.budget.cost_limit_microunits is None
            or self.provider is None
            or self.provider.capability_report.usage != "passed"
        ):
            raise ValueError("Hard cost cap requires measured usage and a limit")
        validate_attempt_configuration(self)
        return self


class ProbeGrant(Record):
    id: UUID
    runner_id: UUID
    provider_id: UUID | None
    provider_config_version: Positive | None
    profile_revision: Positive
    expires_at: AwareDatetime
    actions: list[Literal["probe"]] = Field(min_length=1, max_length=1)


class ProbeDescriptor(Versioned):
    setup_operation_id: UUID
    profile_id: UUID | None
    profile_revision: Positive
    runner_id: UUID
    descriptor_digest: Digest
    configuration_digest: Digest
    adapter: AdapterConfig
    provider: ProviderConfig | None
    grant: ProbeGrant
    workspace_binding: WorkspaceBinding | None = None
    policy: Policy
    budget: Budget

    @model_validator(mode="after")
    def bind_probe(self) -> Self:
        if self.adapter.mode == "endpoint" and self.provider is None:
            raise ValueError("Endpoint probe requires a provider")
        if (
            self.grant.runner_id != self.runner_id
            or self.grant.profile_revision != self.profile_revision
        ):
            raise ValueError("Probe grant does not match the descriptor")
        if self.provider is not None and (
            self.provider.runner_id != self.runner_id
            or self.grant.provider_id != self.provider.id
            or self.grant.provider_config_version != self.provider.config_version
        ):
            raise ValueError("Probe grant does not match the provider revision")
        if self.provider is None and (
            self.grant.provider_id is not None or self.grant.provider_config_version is not None
        ):
            raise ValueError("Provider probe grant requires its provider")
        return self


class VerificationRecord(Record):
    operation_id: UUID
    check_id: Text
    command: list[Text] = Field(min_length=1, max_length=64)
    cwd: Text
    exit_code: StrictInt | None
    started_at: AwareDatetime
    finished_at: AwareDatetime
    tree_hash: GitHash
    artifact_id: UUID
    timed_out: StrictBool = False

    @model_validator(mode="after")
    def ordered_times(self) -> Self:
        if self.finished_at < self.started_at:
            raise ValueError("Verification finish precedes start")
        if self.timed_out and self.exit_code == 0:
            raise ValueError("Timed out verification cannot pass")
        return self


class ArtifactRecord(Record):
    id: UUID
    attempt_id: UUID
    kind: Literal["diff", "summary", "verification", "log", "file"]
    checksum: Digest
    size: Annotated[int, Field(strict=True, ge=0, le=MAX_ARTIFACT_BYTES)]
    media_type: Literal["text/plain", "text/x-diff", "application/json", "application/octet-stream"]
    filename: Annotated[str, Field(min_length=1, max_length=255)]
    expires_at: AwareDatetime


class PushArguments(Record):
    action: Literal["git.push"]
    repository_id: UUID
    remote: Text
    branch: Text
    commit: GitHash
    expected_remote_head: GitHash | None
    force: Literal[False] = False


class DraftPRArguments(Record):
    action: Literal["git.draft_pr"]
    repository_id: UUID
    remote: Text
    base: Text
    head: Text
    commit: GitHash
    title: Text
    body: Annotated[str, Field(max_length=20000)]
    draft: Literal[True] = True


class ToolArguments(Record):
    action: Literal["dependencies.install", "shell.run"]
    repository_id: UUID
    argv: list[Text] = Field(min_length=1, max_length=64)
    cwd: Text
    network: NetworkPolicy


class TeamFindInput(Record):
    tool: Literal["team.find"]
    query: Annotated[str, Field(min_length=1, max_length=100)]
    kinds: Annotated[list[Literal["person", "channel", "group"]], Field(min_length=1, max_length=3)]


class ChannelCreateInput(Record):
    tool: Literal["channel.create"]
    name: Annotated[str, Field(min_length=1, max_length=60)]
    description: Annotated[str, Field(max_length=1024)]
    is_private: StrictBool
    subscriber_user_ids: Annotated[list[Positive], Field(max_length=50)]


class ChannelSubscribeInput(Record):
    tool: Literal["channel.subscribe"]
    channel_id: Positive
    user_ids: Annotated[list[Positive], Field(min_length=1, max_length=50)]


class ChannelUnsubscribeInput(Record):
    tool: Literal["channel.unsubscribe"]
    channel_id: Positive
    user_ids: Annotated[list[Positive], Field(min_length=1, max_length=50)]


class GroupCreateInput(Record):
    tool: Literal["group.create"]
    name: Annotated[str, Field(min_length=1, max_length=100)]
    description: Annotated[str, Field(max_length=1024)]
    member_user_ids: Annotated[list[Positive], Field(max_length=50)]


class GroupAddMembersInput(Record):
    tool: Literal["group.add_members"]
    group_id: Positive
    user_ids: Annotated[list[Positive], Field(min_length=1, max_length=50)]


class GroupRemoveMembersInput(Record):
    tool: Literal["group.remove_members"]
    group_id: Positive
    user_ids: Annotated[list[Positive], Field(min_length=1, max_length=50)]


class TopicPostInput(Record):
    tool: Literal["topic.post"]
    channel_id: Positive
    topic: Annotated[str, Field(min_length=1, max_length=60)]
    content: Annotated[str, Field(min_length=1, max_length=10000)]


class TopicAddPersonInput(Record):
    tool: Literal["topic.add_person"]
    channel_id: Positive
    topic: Annotated[str, Field(min_length=1, max_length=60)]
    user_ids: Annotated[list[Positive], Field(min_length=1, max_length=20)]


class TopicResolveInput(Record):
    tool: Literal["topic.resolve"]
    channel_id: Positive
    topic: Annotated[str, Field(min_length=1, max_length=60)]
    resolved: StrictBool


class TopicMoveInput(Record):
    tool: Literal["topic.move"]
    channel_id: Positive
    topic: Annotated[str, Field(min_length=1, max_length=60)]
    new_topic: Annotated[str, Field(min_length=1, max_length=60)]
    new_channel_id: Positive | None


TeamToolInput = Annotated[
    TeamFindInput
    | ChannelCreateInput
    | ChannelSubscribeInput
    | ChannelUnsubscribeInput
    | GroupCreateInput
    | GroupAddMembersInput
    | GroupRemoveMembersInput
    | TopicPostInput
    | TopicAddPersonInput
    | TopicResolveInput
    | TopicMoveInput,
    Field(discriminator="tool"),
]


class TeamArguments(Record):
    action: Literal["team.manage"]
    input: TeamToolInput


ApprovalArguments = Annotated[
    PushArguments | DraftPRArguments | ToolArguments | TeamArguments, Field(discriminator="action")
]


class ContextReadArguments(Record):
    action: Literal["context.read"]
    context_ids: list[UUID] = Field(min_length=1, max_length=100)


class RepositoryReadArguments(Record):
    action: Literal["repository.read"]
    repository_id: UUID
    paths: list[Text] = Field(min_length=1, max_length=100)


class RepositoryEditArguments(Record):
    action: Literal["repository.edit"]
    repository_id: UUID
    patch_artifact_id: UUID
    patch_checksum: Digest
    expected_tree: GitHash


class ChecksArguments(Record):
    action: Literal["checks.run"]
    repository_id: UUID
    check_ids: list[Text] = Field(min_length=1, max_length=100)
    tree_hash: GitHash


class CommitArguments(Record):
    action: Literal["git.commit"]
    repository_id: UUID
    message: Annotated[str, Field(min_length=1, max_length=20000)]
    tree_hash: GitHash
    expected_parent: GitHash


OperationArguments = Annotated[
    PushArguments
    | DraftPRArguments
    | ToolArguments
    | ContextReadArguments
    | RepositoryReadArguments
    | RepositoryEditArguments
    | ChecksArguments
    | CommitArguments
    | TeamArguments,
    Field(discriminator="action"),
]


class ApprovalRecord(LeaseIdentity, Versioned):
    id: UUID
    operation_id: UUID
    operation_hash: Digest
    policy_version: Positive
    version: Positive
    arguments: ApprovalArguments
    tree_hash: GitHash | None
    approver_user_id: Positive | None
    decision: ApprovalState
    expires_at: AwareDatetime
    nonce: UUID


class ProcessPayload(Record):
    process_state: ProcessState
    adapter_session_ref: str | None = None
    stop_confirmed: StrictBool = False
    summary: Annotated[str, Field(max_length=2048)] = ""


class ToolPayload(Record):
    operation_id: UUID
    tool_class: ExecutionAction
    argument_digest: Digest
    status: OperationState
    artifact_id: UUID | None = None
    exit_code: StrictInt | None = None
    summary: Annotated[str, Field(max_length=2048)] = ""


class InputPayload(Record):
    input_id: UUID
    input_sequence: Positive
    delivery_state: InputState


class InputRequestPayload(Record):
    question: Annotated[str, Field(min_length=1, max_length=2048)]
    options: list[Text] = Field(default_factory=list, max_length=10)


class ResultPayload(Record):
    artifact_ids: list[UUID] = Field(min_length=1, max_length=100)
    tree_hash: GitHash | None = None
    summary: Annotated[str, Field(max_length=4096)]


class WorkspacePreparedPayload(Record):
    repository_id: UUID
    workspace_reference: Annotated[str, Field(pattern=r"^[a-zA-Z0-9_-]{1,200}$")]
    base_ref: Text
    base_commit: GitHash
    tree_hash: GitHash
    user_worktree_dirty: StrictBool


class RunnerEvent(LeaseIdentity, Versioned):
    event_id: UUID
    sequence: Positive
    type: Literal[
        "workspace.prepared",
        "attempt.starting",
        "attempt.started",
        "attempt.stopped",
        "attempt.interrupted",
        "input.applied",
        "input.delivery_uncertain",
        "tool.started",
        "tool.finished",
        "verification.finished",
        "input.requested",
        "result.prepared",
    ]
    occurred_at: AwareDatetime
    payload: (
        ProcessPayload
        | ToolPayload
        | InputPayload
        | VerificationRecord
        | InputRequestPayload
        | ResultPayload
        | WorkspacePreparedPayload
    )

    @model_validator(mode="before")
    @classmethod
    def typed_event_payload(cls, data: object) -> object:
        if not isinstance(data, dict):
            return data
        models: dict[str, type[BaseModel]] = {
            "workspace.prepared": WorkspacePreparedPayload,
            "attempt.starting": ProcessPayload,
            "attempt.started": ProcessPayload,
            "attempt.stopped": ProcessPayload,
            "attempt.interrupted": ProcessPayload,
            "tool.started": ToolPayload,
            "tool.finished": ToolPayload,
            "input.applied": InputPayload,
            "input.delivery_uncertain": InputPayload,
            "verification.finished": VerificationRecord,
            "input.requested": InputRequestPayload,
            "result.prepared": ResultPayload,
        }
        event_type = data.get("type")
        if not isinstance(event_type, str):
            raise ValueError("Event type must be a string")
        model = models.get(event_type)
        if model is None:
            raise ValueError("Event is not runner authority")
        return {**data, "payload": model.model_validate(data.get("payload"))}

    @model_validator(mode="after")
    def check_event_state(self) -> Self:
        if isinstance(self.payload, ProcessPayload):
            expected = {
                "attempt.starting": "starting",
                "attempt.started": "active",
                "attempt.stopped": "stopped",
                "attempt.interrupted": "unknown",
            }
            if self.payload.process_state != expected[self.type]:
                raise ValueError("Process event state mismatch")
            if self.type == "attempt.stopped" and not self.payload.stop_confirmed:
                raise ValueError("Stopped event requires stop confirmation")
            if self.type != "attempt.stopped" and self.payload.stop_confirmed:
                raise ValueError("Only a stopped event can confirm a stop")
        if isinstance(self.payload, InputPayload):
            expected_input = {
                "input.applied": "applied",
                "input.delivery_uncertain": "delivery_uncertain",
            }
            if self.payload.delivery_state != expected_input[self.type]:
                raise ValueError("Input event state mismatch")
        if isinstance(self.payload, ToolPayload):
            allowed = (
                {"started"}
                if self.type == "tool.started"
                else {"succeeded", "failed", "outcome_unknown", "cancelled"}
            )
            if self.payload.status not in allowed:
                raise ValueError("Tool event state mismatch")
            if self.type == "tool.started" and (
                self.payload.exit_code is not None or self.payload.artifact_id is not None
            ):
                raise ValueError("Started tool event cannot contain its outcome")
            if self.payload.status == "succeeded" and self.payload.exit_code not in (None, 0):
                raise ValueError("Successful tool cannot have a failing exit code")
        return self


class ClaimRequest(Versioned):
    claim_key: UUID
    capacity: Annotated[int, Field(strict=True, ge=1, le=1)] = 1
    runner_version: Text


class ClaimResponse(Versioned):
    result: Literal["success"] = "success"
    msg: Literal[""] = ""
    attempt: AttemptDescriptor | None = None
    job_version: Positive | None = None
    probe: ProbeDescriptor | None = None

    @model_validator(mode="after")
    def single_work_item(self) -> Self:
        if self.attempt is not None and self.probe is not None:
            raise ValueError("A claim returns at most one work item")
        return self


class JobRecord(Versioned):
    id: UUID
    status: JobState
    phase: Phase
    job_kind: JobKind
    delivery_target: DeliveryTarget
    version: Positive

    @model_validator(mode="after")
    def target_matches_kind(self) -> Self:
        if (self.job_kind in ("answer", "manage")) != (self.delivery_target == "answer"):
            raise ValueError("Delivery target does not match job kind")
        return self


class LocalOperationReceipt(Record):
    operation_id: UUID
    argument_digest: Digest
    base_commit: GitHash | None
    tree_hash: GitHash | None
    outcome: Literal["succeeded", "failed", "no_effect"]
    observed_at: AwareDatetime


class RemoteReceipt(Record):
    remote: Text
    branch: Text
    commit: GitHash
    operation_id: UUID
    pull_request_id: str | None = None
    pull_request_url: str | None = None
    observed_at: AwareDatetime


class JobStatePayload(Record):
    status: JobState
    reason: Annotated[str, Field(max_length=200)] = ""


class ApprovalPayload(Record):
    approval_id: UUID
    operation_hash: Digest
    version: Positive
    decision: ApprovalState


class PublicationPayload(Record):
    result_message_id: Positive | None = None
    reason: Annotated[str, Field(max_length=200)] = ""


class TeamReceiptObjects(Record):
    channel_id: Positive | None = None
    group_id: Positive | None = None
    message_id: Positive | None = None
    user_ids: list[Positive] | None = None


class TeamReceipt(Record):
    tool: TeamToolId
    outcome: Literal["succeeded", "failed"]
    summary: Annotated[str, Field(max_length=2048)]
    objects: TeamReceiptObjects
    error: Annotated[str, Field(max_length=2048)] | None


class TeamExecutedPayload(TeamReceipt):
    operation_id: UUID


class AuthorityEvent(Versioned):
    job_id: UUID
    attempt_id: UUID | None = None
    lease_epoch: Positive | None = None
    event_id: UUID
    sequence: Positive
    type: Literal[
        "job.queued",
        "attempt.starting",
        "attempt.interrupted",
        "input.received",
        "approval.requested",
        "approval.resolved",
        "attempt.stop_requested",
        "result.published",
        "publication.blocked",
        "job.completed",
        "team.executed",
    ]
    occurred_at: AwareDatetime
    payload: (
        JobStatePayload | InputPayload | ApprovalPayload | PublicationPayload | TeamExecutedPayload
    )

    @model_validator(mode="before")
    @classmethod
    def typed_authority_payload(cls, data: object) -> object:
        if not isinstance(data, dict):
            return data
        models: dict[str, type[BaseModel]] = {
            "job.queued": JobStatePayload,
            "attempt.starting": JobStatePayload,
            "attempt.interrupted": JobStatePayload,
            "job.completed": JobStatePayload,
            "attempt.stop_requested": JobStatePayload,
            "input.received": InputPayload,
            "approval.requested": ApprovalPayload,
            "approval.resolved": ApprovalPayload,
            "result.published": PublicationPayload,
            "publication.blocked": PublicationPayload,
            "team.executed": TeamExecutedPayload,
        }
        event_type = data.get("type")
        if not isinstance(event_type, str):
            raise ValueError("Event type must be a string")
        model = models.get(event_type)
        if model is None:
            raise ValueError("Unknown authority event")
        return {**data, "payload": model.model_validate(data.get("payload"))}

    @model_validator(mode="after")
    def check_event_state(self) -> Self:
        if (self.attempt_id is None) != (self.lease_epoch is None):
            raise ValueError("Attempt event identity requires its lease epoch")
        if isinstance(self.payload, JobStatePayload):
            expected = {
                "job.queued": "queued",
                "attempt.starting": "running",
                "attempt.interrupted": "interrupted",
                "job.completed": "completed",
                "attempt.stop_requested": "cancel_requested",
            }
            if self.payload.status != expected[self.type]:
                raise ValueError("Job event state mismatch")
        if isinstance(self.payload, InputPayload) and self.payload.delivery_state != "pending":
            raise ValueError("Received input must be pending")
        if isinstance(self.payload, ApprovalPayload):
            allowed = (
                {"pending"}
                if self.type == "approval.requested"
                else {"approved", "rejected", "expired", "cancelled", "consumed"}
            )
            if self.payload.decision not in allowed:
                raise ValueError("Approval event decision mismatch")
        if isinstance(self.payload, PublicationPayload):
            if self.type == "result.published" and self.payload.result_message_id is None:
                raise ValueError("Published result requires a message")
            if self.type == "publication.blocked" and (
                self.payload.result_message_id is not None or not self.payload.reason
            ):
                raise ValueError("Blocked publication requires a reason and no message")
        return self


EVENT_AUTHORITIES = {
    "server": {
        "job.queued",
        "attempt.starting",
        "attempt.interrupted",
        "input.received",
        "approval.requested",
        "approval.resolved",
        "attempt.stop_requested",
        "team.executed",
    },
    "verifier": {"job.completed"},
    "publisher": {"result.published", "publication.blocked"},
}


def parse_authority_event(authority: str, value: object) -> RunnerEvent | AuthorityEvent:
    if authority == "runner":
        return parse_runner_event(value)
    if len(canonical_json(value)) > MAX_EVENT_BYTES:
        raise ValueError("Agent event exceeds 64 KiB")
    event = AuthorityEvent.model_validate(value)
    if event.type not in EVENT_AUTHORITIES.get(authority, set()):
        raise ValueError("Event authority does not match its type")
    return event


def derive_execution_configuration(
    value: AttemptDescriptor | ProbeDescriptor,
) -> ExecutionConfiguration:
    provider = None
    if value.provider is not None:
        provider = EffectiveProvider.model_validate(
            value.provider.model_dump(include=set(EffectiveProvider.model_fields))
        )
    if isinstance(value, ProbeDescriptor):
        binding = value.workspace_binding
    elif value.repository is not None:
        binding = WorkspaceBinding(
            canonical_origin=value.repository.canonical_origin,
            repository_id=value.repository.id,
            workspace_alias=value.repository.workspace_alias,
            policy_version=value.repository.policy_version,
            allowed_refs=value.repository.allowed_refs,
            checks_digest=hashlib.sha256(
                canonical_json(
                    [serialize_payload(check) for check in value.repository.required_checks]
                )
            ).hexdigest(),
        )
    else:
        binding = None
    return ExecutionConfiguration(
        runner_id=value.runner_id,
        profile_revision=value.profile_revision,
        adapter=value.adapter,
        provider=provider,
        workspace_binding=binding,
        policy_version=value.policy.version,
        actions=sorted(set(value.policy.actions)),
        sandbox=value.policy.sandbox,
        network=value.policy.network,
        hard_cost_cap=value.policy.hard_cost_cap,
        budget=value.budget,
    )


def validate_attempt_configuration(value: AttemptDescriptor) -> None:
    """Permit narrower lease authority within the unchanged tested runtime."""
    tested = value.tested_configuration
    effective = derive_execution_configuration(value)
    for name in [
        "runner_id",
        "profile_revision",
        "adapter",
        "provider",
        "policy_version",
        "sandbox",
        "network",
    ]:
        if getattr(effective, name) != getattr(tested, name):
            raise ValueError("Attempt runtime differs from its tested configuration")
    if effective.workspace_binding != tested.workspace_binding and not (
        value.job_kind == "answer" and effective.workspace_binding is None
    ):
        raise ValueError("Attempt repository differs from its tested configuration")
    if not set(effective.actions) <= set(tested.actions):
        raise ValueError("Attempt actions exceed the tested configuration")
    if tested.hard_cost_cap and not effective.hard_cost_cap:
        raise ValueError("Attempt cannot remove the tested hard cost cap")
    for name in Budget.model_fields:
        ceiling = getattr(tested.budget, name)
        actual = getattr(effective.budget, name)
        if ceiling is not None and (actual is None or actual > ceiling):
            raise ValueError("Attempt budget exceeds the tested configuration")


def execution_configuration(value: AttemptDescriptor | ProbeDescriptor) -> ExecutionConfiguration:
    if isinstance(value, AttemptDescriptor):
        validate_attempt_configuration(value)
        return value.tested_configuration
    return derive_execution_configuration(value)


def configuration_digest(value: AttemptDescriptor | ProbeDescriptor) -> str:
    return hashlib.sha256(
        canonical_json(serialize_payload(execution_configuration(value)))
    ).hexdigest()


PAYLOAD_MODELS: dict[str, type[BaseModel]] = {
    "audience": AudienceBinding,
    "runner_catalog": RunnerCatalog,
    "sandbox": SandboxConfig,
    "profile": ProfileConfig,
    "provider": ProviderConfig,
    "repository": RepositoryConfig,
    "grant": GrantConfig,
    "attempt_descriptor": AttemptDescriptor,
    "probe_descriptor": ProbeDescriptor,
    "runner_event": RunnerEvent,
    "approval": ApprovalRecord,
    "artifact": ArtifactRecord,
    "verification": VerificationRecord,
    "readiness": ReadinessReport,
    "claim_request": ClaimRequest,
    "claim_response": ClaimResponse,
    "job": JobRecord,
    "policy": Policy,
    "budget": Budget,
    "configuration": ExecutionConfiguration,
    "authority_event": AuthorityEvent,
    "remote_receipt": RemoteReceipt,
    "local_operation_receipt": LocalOperationReceipt,
    "context_ref": ContextReference,
    "input": InputRecord,
    "checkpoint": Checkpoint,
}


def serialize_payload(value: BaseModel) -> dict[str, object]:
    return value.model_dump(mode="json")


def canonical_json(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode()


def descriptor_digest(value: dict[str, object]) -> str:
    return hashlib.sha256(
        canonical_json({key: item for key, item in value.items() if key != "descriptor_digest"})
    ).hexdigest()


def parse_runner_event(value: object) -> RunnerEvent:
    if len(canonical_json(value)) > MAX_EVENT_BYTES:
        raise ValueError("Agent event exceeds 64 KiB")
    return RunnerEvent.model_validate(value)


def parse_payload(schema: str, value: object) -> BaseModel:
    if schema == "operation_arguments":
        return TypeAdapter(OperationArguments).validate_python(value)
    if schema == "approval_arguments":
        return TypeAdapter(ApprovalArguments).validate_python(value)
    if schema == "runner_event":
        return parse_runner_event(value)
    model = PAYLOAD_MODELS.get(schema)
    if model is None:
        raise ValueError("Unsupported agent schema")
    return model.model_validate(value)


def protocol_json_schemas() -> dict[str, object]:
    return {
        **{name: model.model_json_schema() for name, model in PAYLOAD_MODELS.items()},
        "operation_arguments": TypeAdapter(OperationArguments).json_schema(),
        "approval_arguments": TypeAdapter(ApprovalArguments).json_schema(),
    }
