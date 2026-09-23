"""Durable agent records. Actions enforce permissions and state transitions."""

import uuid
from typing import ClassVar, get_args

from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.utils.timezone import now as timezone_now
from pydantic import TypeAdapter
from pydantic import ValidationError as ProtocolValidationError
from typing_extensions import override

from zerver.lib import agent_protocol as protocol


def choices(values: object) -> list[tuple[str, str]]:
    return [(value, value) for value in get_args(values)]


def state_constraint(field: str, values: object, name: str) -> models.CheckConstraint:
    return models.CheckConstraint(condition=Q(**{f"{field}__in": get_args(values)}), name=name)


def validate_agent_references(instance: models.Model) -> None:
    """Reject references outside the record realm before a service writes it."""
    realm_id = getattr(instance, "realm_id", None)
    for field in instance._meta.fields:
        if (
            not isinstance(field, (models.ForeignKey, models.OneToOneField))
            or field.name == "realm"
        ):
            continue
        if getattr(instance, field.attname) is None:
            continue
        related = getattr(instance, field.name)
        related_realm_id = getattr(related, "realm_id", None)
        if related_realm_id is not None and related_realm_id != realm_id:
            raise ValidationError({field.name: "Agent reference belongs to another realm."})

    attempt = getattr(instance, "attempt", None)
    job_id = getattr(instance, "job_id", None)
    if attempt is not None and job_id is not None and attempt.job_id != job_id:
        raise ValidationError("Attempt does not belong to this job.")
    delivered = getattr(instance, "delivered_attempt", None)
    if delivered is not None and delivered.job_id != job_id:
        raise ValidationError("Input attempt does not belong to this job.")
    checkpoint = getattr(instance, "source_checkpoint", None)
    if checkpoint is not None and checkpoint.attempt.job_id != job_id:
        raise ValidationError("Checkpoint does not belong to this job.")
    resume = getattr(instance, "resume_checkpoint", None)
    if resume is not None and resume.attempt.job_id != instance.pk:
        raise ValidationError("Resume checkpoint does not belong to this job.")
    operation = getattr(instance, "operation", None)
    if operation is not None and operation.attempt_id != getattr(instance, "attempt_id", None):
        raise ValidationError("Operation does not belong to this attempt.")
    artifact = getattr(instance, "output_artifact", None)
    if artifact is not None and artifact.attempt_id != getattr(instance, "attempt_id", None):
        raise ValidationError("Verification artifact does not belong to this attempt.")


class AgentRecord(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    realm = models.ForeignKey("zerver.Realm", on_delete=models.CASCADE)
    created_at = models.DateTimeField(default=timezone_now)
    updated_at = models.DateTimeField(default=timezone_now)
    protocol_fields: ClassVar[dict[str, object]] = {}

    class Meta:
        abstract = True

    @override
    def clean(self) -> None:
        super().clean()
        validate_agent_references(self)
        for name, schema in self.protocol_fields.items():
            try:
                TypeAdapter(schema).validate_python(getattr(self, name))
            except ProtocolValidationError:
                # Do not include rejected values, which can contain secrets.
                raise ValidationError({name: "Invalid agent protocol data."}) from None


class AgentRealmSettings(AgentRecord):
    realm = models.OneToOneField("zerver.Realm", on_delete=models.CASCADE)
    enabled = models.BooleanField(default=False)
    retention_cleanup_enabled = models.BooleanField(default=False)
    revision = models.PositiveIntegerField(default=1)
    default_profile = models.ForeignKey(
        "AgentProfile", on_delete=models.PROTECT, null=True, related_name="default_for_realms"
    )
    default_selection_revision = models.PositiveIntegerField(default=1)
    default_selected_by = models.ForeignKey(
        "zerver.UserProfile",
        on_delete=models.PROTECT,
        null=True,
        related_name="agent_defaults_selected",
    )
    default_selected_at = models.DateTimeField(null=True)
    active_job_limit = models.PositiveIntegerField(default=2)
    queued_job_limit = models.PositiveIntegerField(default=100)
    profile_queue_limit = models.PositiveIntegerField(default=20)
    event_retention_days = models.PositiveIntegerField(default=30)
    artifact_retention_days = models.PositiveIntegerField(default=7)
    approval_retention_days = models.PositiveIntegerField(default=90)
    send_retention_days = models.PositiveIntegerField(default=30)


class AgentRunner(AgentRecord):
    owner = models.ForeignKey("zerver.UserProfile", on_delete=models.PROTECT)
    name = models.CharField(max_length=200)
    host_kind = models.CharField(
        max_length=20,
        choices=[("workstation", "workstation"), ("server", "server"), ("unknown", "unknown")],
        default="unknown",
    )
    metadata_revision = models.PositiveIntegerField(default=1)
    fingerprint = models.CharField(max_length=128)
    version = models.CharField(max_length=100, default="")
    platform = models.CharField(max_length=100, default="")
    status = models.CharField(
        max_length=20, choices=choices(protocol.RunnerState), default="unknown"
    )
    last_heartbeat_at = models.DateTimeField(null=True)
    revoked_at = models.DateTimeField(null=True)
    capacity = models.PositiveIntegerField(default=1)
    policy_version = models.PositiveIntegerField(default=1)
    catalog_revision = models.PositiveIntegerField(default=1)
    catalog_report = models.JSONField(default=dict)
    protocol_fields = {"catalog_report": protocol.RunnerCatalog}

    class Meta:
        constraints = [
            state_constraint("status", protocol.RunnerState, "agent_runner_state_valid"),
            models.CheckConstraint(
                condition=Q(host_kind__in=["workstation", "server", "unknown"]),
                name="agent_runner_host_kind_valid",
            ),
        ]
        indexes = [models.Index(fields=["realm", "owner", "status"])]


class AgentPairing(AgentRecord):
    # Anonymous requests have no tenant authority until browser approval.
    realm = models.ForeignKey("zerver.Realm", on_delete=models.CASCADE, null=True)  # type: ignore[assignment]  # Pairing precedes tenant approval.
    owner = models.ForeignKey("zerver.UserProfile", on_delete=models.PROTECT, null=True)
    runner = models.OneToOneField(AgentRunner, on_delete=models.PROTECT, null=True)
    device_name = models.CharField(max_length=200)
    fingerprint = models.CharField(max_length=128)
    polling_secret_hash = models.CharField(max_length=128, unique=True)
    user_code_hash = models.CharField(max_length=128, unique=True)
    expires_at = models.DateTimeField()
    approved_at = models.DateTimeField(null=True)
    exchanged_at = models.DateTimeField(null=True)
    failed_attempts = models.PositiveIntegerField(default=0)
    state = models.CharField(max_length=20, default="pending")

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=Q(state__in=["pending", "approved", "exchanged", "expired", "rejected"]),
                name="agent_pairing_state_valid",
            ),
            models.CheckConstraint(
                condition=(
                    Q(
                        realm__isnull=True,
                        owner__isnull=True,
                        approved_at__isnull=True,
                        runner__isnull=True,
                    )
                    | Q(realm__isnull=False, owner__isnull=False, approved_at__isnull=False)
                ),
                name="agent_pairing_tenant_authority",
            ),
            models.CheckConstraint(
                condition=~Q(state__in=["approved", "exchanged"])
                | Q(realm__isnull=False, owner__isnull=False, approved_at__isnull=False),
                name="agent_pairing_approval_bound",
            ),
            models.CheckConstraint(
                condition=~Q(state="exchanged")
                | Q(runner__isnull=False, exchanged_at__isnull=False),
                name="agent_pairing_exchange_bound",
            ),
        ]


class AgentRunnerCredential(AgentRecord):
    runner = models.ForeignKey(AgentRunner, on_delete=models.PROTECT)
    token_hash = models.CharField(max_length=128, unique=True)
    refresh_hash = models.CharField(max_length=128, unique=True)
    expires_at = models.DateTimeField()
    refresh_expires_at = models.DateTimeField()
    revoked_at = models.DateTimeField(null=True)
    rotated_at = models.DateTimeField(null=True)
    parent = models.OneToOneField("self", on_delete=models.PROTECT, null=True)


class AgentSecret(AgentRecord):
    owner = models.ForeignKey("zerver.UserProfile", on_delete=models.PROTECT)
    ciphertext = models.BinaryField()
    wrapped_key = models.BinaryField()
    key_id = models.CharField(max_length=200)
    encryption_version = models.PositiveIntegerField(default=1)
    secret_type = models.CharField(max_length=40, default="provider")
    version = models.PositiveIntegerField(default=1)
    revoked_at = models.DateTimeField(null=True)


class AgentProvider(AgentRecord):
    owner = models.ForeignKey("zerver.UserProfile", on_delete=models.PROTECT)
    runner = models.ForeignKey(AgentRunner, on_delete=models.PROTECT)
    name = models.CharField(max_length=200)
    base_url = models.URLField(max_length=2048)
    api_mode = models.CharField(max_length=30, default="chat_completions")
    model_id = models.CharField(max_length=200)
    allowed_models = models.JSONField(default=list)
    secret = models.ForeignKey(AgentSecret, on_delete=models.PROTECT, null=True)
    local_credential_ref = models.CharField(max_length=200, default="")
    config_version = models.PositiveIntegerField(default=1)
    metadata_revision = models.PositiveIntegerField(default=1)
    context_window_tokens = models.PositiveIntegerField()
    max_output_tokens = models.PositiveIntegerField()
    data_scope = models.JSONField(default=list)
    network_policy = models.JSONField(default=dict)
    capability_report = models.JSONField(default=dict)
    disabled_at = models.DateTimeField(null=True)
    protocol_fields = {
        "network_policy": protocol.NetworkPolicy,
        "capability_report": protocol.CapabilityReport,
        "allowed_models": list[str],
        "data_scope": list[str],
    }

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=Q(api_mode__in=["chat_completions", "responses"]),
                name="agent_provider_api_valid",
            ),
            models.CheckConstraint(
                condition=Q(secret__isnull=True) | Q(local_credential_ref=""),
                name="agent_provider_one_secret",
            ),
            models.CheckConstraint(
                condition=Q(config_version__gte=1), name="agent_provider_version_positive"
            ),
        ]


class AgentRepository(AgentRecord):
    owner = models.ForeignKey("zerver.UserProfile", on_delete=models.PROTECT)
    runner = models.ForeignKey(AgentRunner, on_delete=models.PROTECT)
    workspace_alias = models.CharField(max_length=80)
    canonical_origin = models.CharField(max_length=2048, default="")
    allowed_refs = models.JSONField(default=list)
    required_checks = models.JSONField(default=list)
    policy_version = models.PositiveIntegerField(default=1)
    disabled_at = models.DateTimeField(null=True)
    protocol_fields = {"required_checks": list[protocol.RequiredCheck], "allowed_refs": list[str]}

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["runner", "workspace_alias"], name="agent_repo_runner_alias_unique"
            )
        ]


class AgentProfile(AgentRecord):
    owner = models.ForeignKey("zerver.UserProfile", on_delete=models.PROTECT)
    bot_user = models.OneToOneField(
        "zerver.UserProfile", on_delete=models.PROTECT, related_name="agent_profile"
    )
    runner = models.ForeignKey(AgentRunner, on_delete=models.PROTECT)
    name = models.CharField(max_length=200)
    description = models.TextField(default="")
    mode = models.CharField(max_length=20, default="acp")
    adapter_id = models.CharField(max_length=80)
    adapter_version = models.CharField(max_length=100)
    provider = models.ForeignKey(AgentProvider, on_delete=models.PROTECT, null=True)
    revision = models.PositiveIntegerField(default=1)
    metadata_revision = models.PositiveIntegerField(default=1)
    policy_version = models.PositiveIntegerField(default=1)
    desired_state = models.CharField(
        max_length=20, choices=choices(protocol.ProfileState), default="draft"
    )
    default_mode = models.CharField(
        max_length=20, choices=choices(protocol.JobKind), default="answer"
    )
    default_repository = models.ForeignKey(AgentRepository, on_delete=models.PROTECT, null=True)
    readiness_state = models.CharField(
        max_length=20, choices=choices(protocol.ReadinessState), default="unchecked"
    )
    enabled_revision = models.PositiveIntegerField(null=True)
    readiness_revision = models.PositiveIntegerField(null=True)
    readiness_digest = models.CharField(max_length=64, default="")
    readiness_configuration_digest = models.CharField(max_length=64, default="")
    readiness_configuration = models.JSONField(default=None, null=True)
    capability_report = models.JSONField(default=dict)
    archived_at = models.DateTimeField(null=True)
    policy = models.JSONField(default=dict)
    budget = models.JSONField(default=dict)
    protocol_fields = {
        "capability_report": protocol.CapabilityReport,
        "readiness_configuration": protocol.ExecutionConfiguration | None,
        "policy": protocol.Policy,
        "budget": protocol.Budget,
    }

    class Meta:
        constraints = [
            state_constraint("desired_state", protocol.ProfileState, "agent_profile_state_valid"),
            state_constraint(
                "readiness_state", protocol.ReadinessState, "agent_profile_readiness_valid"
            ),
            state_constraint("default_mode", protocol.JobKind, "agent_profile_job_kind_valid"),
            models.CheckConstraint(
                condition=Q(mode="acp") | Q(mode="endpoint", provider__isnull=False),
                name="agent_profile_runtime_valid",
            ),
            models.CheckConstraint(
                condition=~Q(desired_state="enabled") | Q(enabled_revision__isnull=False),
                name="agent_profile_enable_revision",
            ),
            models.CheckConstraint(
                condition=Q(enabled_revision__isnull=True)
                | Q(enabled_revision__lte=models.F("revision")),
                name="agent_profile_enabled_not_future",
            ),
            models.CheckConstraint(
                condition=Q(readiness_revision__isnull=True)
                | Q(readiness_revision__lte=models.F("revision")),
                name="agent_profile_ready_not_future",
            ),
            models.CheckConstraint(
                condition=Q(revision__gte=1, policy_version__gte=1),
                name="agent_profile_revision_positive",
            ),
        ]


class AgentGrant(AgentRecord):
    owner = models.ForeignKey(
        "zerver.UserProfile", on_delete=models.PROTECT, related_name="owned_agent_grants"
    )
    principal_user = models.ForeignKey(
        "zerver.UserProfile", on_delete=models.PROTECT, null=True, related_name="agent_grants"
    )
    principal_group = models.ForeignKey("zerver.UserGroup", on_delete=models.PROTECT, null=True)
    target_kind = models.CharField(max_length=20, default="profile")
    runner = models.ForeignKey(AgentRunner, on_delete=models.PROTECT, null=True)
    provider = models.ForeignKey(AgentProvider, on_delete=models.PROTECT, null=True)
    profile = models.ForeignKey(AgentProfile, on_delete=models.PROTECT, null=True)
    repository = models.ForeignKey(AgentRepository, on_delete=models.PROTECT, null=True)
    scope = models.JSONField(default=None, null=True)
    actions = models.JSONField(default=list)
    policy_version = models.PositiveIntegerField(default=1)
    expires_at = models.DateTimeField(null=True)
    revoked_at = models.DateTimeField(null=True)
    protocol_fields = {"scope": protocol.ConversationScope | None, "actions": list[protocol.Action]}

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=(
                    Q(
                        target_kind="runner",
                        runner__isnull=False,
                        provider__isnull=True,
                        profile__isnull=True,
                        repository__isnull=True,
                    )
                    | Q(
                        target_kind="provider",
                        provider__isnull=False,
                        runner__isnull=True,
                        profile__isnull=True,
                        repository__isnull=True,
                    )
                    | Q(
                        target_kind="repository",
                        repository__isnull=False,
                        runner__isnull=True,
                        provider__isnull=True,
                        profile__isnull=True,
                    )
                    | Q(
                        target_kind="profile",
                        profile__isnull=False,
                        runner__isnull=True,
                        provider__isnull=True,
                    )
                ),
                name="agent_grant_target_valid",
            ),
            models.CheckConstraint(
                condition=(
                    Q(principal_user__isnull=False, principal_group__isnull=True)
                    | Q(principal_user__isnull=True, principal_group__isnull=False)
                ),
                name="agent_grant_one_principal",
            ),
        ]

    @override
    def clean(self) -> None:
        super().clean()
        try:
            protocol.GrantConfig.model_validate(
                {
                    "id": self.id,
                    "principal_user_id": self.principal_user_id,
                    "principal_group_id": self.principal_group_id,
                    "target_kind": self.target_kind,
                    "runner_id": self.runner_id,
                    "provider_id": self.provider_id,
                    "profile_id": self.profile_id,
                    "repository_id": self.repository_id,
                    "scope": self.scope,
                    "actions": self.actions,
                    "policy_version": self.policy_version,
                    "expires_at": self.expires_at,
                }
            )
        except ProtocolValidationError:
            raise ValidationError("Invalid agent grant.") from None


class AgentConversation(AgentRecord):
    audience_epoch = models.PositiveBigIntegerField(default=1)
    audience_binding = models.JSONField(null=True, default=None)
    profile = models.ForeignKey(AgentProfile, on_delete=models.PROTECT)
    repository = models.ForeignKey(AgentRepository, on_delete=models.PROTECT, null=True)
    anchor_message = models.ForeignKey("zerver.Message", on_delete=models.SET_NULL, null=True)
    scope = models.JSONField(default=dict)
    session_reference = models.CharField(max_length=512, default="")
    protocol_fields = {
        "scope": protocol.ConversationScope,
        "audience_binding": protocol.AudienceBinding | None,
    }


class AgentJob(AgentRecord):
    resume_checkpoint = models.ForeignKey(
        "AgentCheckpoint", on_delete=models.PROTECT, null=True, related_name="resume_jobs"
    )
    draft_completion = models.JSONField(null=True, default=None)
    event_sequence = models.PositiveBigIntegerField(default=0)
    input_sequence = models.PositiveBigIntegerField(default=0)
    result_proposal = models.JSONField(null=True, default=None)
    result_receipt = models.JSONField(null=True, default=None)
    stop_target = models.CharField(max_length=20, default="cancelled")
    requester = models.ForeignKey("zerver.UserProfile", on_delete=models.PROTECT)
    conversation = models.ForeignKey(AgentConversation, on_delete=models.PROTECT)
    profile = models.ForeignKey(AgentProfile, on_delete=models.PROTECT)
    runner = models.ForeignKey(AgentRunner, on_delete=models.PROTECT)
    repository = models.ForeignKey(AgentRepository, on_delete=models.PROTECT, null=True)
    source_message = models.ForeignKey(
        "zerver.Message", on_delete=models.SET_NULL, null=True, related_name="agent_source_jobs"
    )
    trigger_kind = models.CharField(
        max_length=30, choices=choices(protocol.TriggerKind), default="manual"
    )
    job_kind = models.CharField(max_length=20, choices=choices(protocol.JobKind), default="answer")
    delivery_target = models.CharField(
        max_length=20, choices=choices(protocol.DeliveryTarget), default="answer"
    )
    request = models.TextField()
    base_ref = models.CharField(max_length=200, default="")
    idempotency_key = models.UUIDField()
    payload_digest = models.CharField(max_length=64)
    admission_revision = models.PositiveIntegerField(default=1)
    status = models.CharField(max_length=30, choices=choices(protocol.JobState), default="draft")
    phase = models.CharField(max_length=20, choices=choices(protocol.Phase), default="inspect")
    version = models.PositiveIntegerField(default=1)
    blocked_reason = models.CharField(max_length=200, default="")
    start_deadline = models.DateTimeField(null=True)
    next_attempt_at = models.DateTimeField(default=timezone_now)
    result_message = models.ForeignKey(
        "zerver.Message", on_delete=models.SET_NULL, null=True, related_name="agent_result_jobs"
    )
    completed_at = models.DateTimeField(null=True)
    policy = models.JSONField(default=dict)
    budget = models.JSONField(default=dict)
    protocol_fields = {"policy": protocol.Policy, "budget": protocol.Budget}

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["realm", "requester", "idempotency_key"],
                name="agent_job_idempotency_unique",
            ),
            models.UniqueConstraint(
                fields=["realm", "source_message", "profile", "trigger_kind"],
                condition=Q(source_message__isnull=False),
                name="agent_job_trigger_unique",
            ),
            state_constraint("status", protocol.JobState, "agent_job_state_valid"),
            state_constraint("phase", protocol.Phase, "agent_job_phase_valid"),
            models.CheckConstraint(
                condition=Q(job_kind="answer", delivery_target="answer")
                | Q(job_kind="code", delivery_target__in=["patch", "draft_pr"])
                | Q(job_kind="manage", delivery_target="answer"),
                name="agent_job_kind_target_valid",
            ),
            models.CheckConstraint(
                condition=Q(version__gte=1, admission_revision__gte=1),
                name="agent_job_version_positive",
            ),
        ]
        indexes = [
            models.Index(fields=["realm", "status", "runner", "next_attempt_at"]),
            models.Index(fields=["profile", "status"]),
        ]


class AgentAttempt(AgentRecord):
    audience_binding = models.JSONField(null=True, default=None)
    tool_rounds = models.PositiveIntegerField(default=0)
    stop_receipt = models.JSONField(null=True, default=None)
    job = models.ForeignKey(AgentJob, on_delete=models.PROTECT)
    runner = models.ForeignKey(AgentRunner, on_delete=models.PROTECT)
    number = models.PositiveIntegerField()
    active = models.BooleanField(default=True)
    lease_epoch = models.PositiveBigIntegerField()
    lease_expires_at = models.DateTimeField()
    claim_key = models.UUIDField(default=uuid.uuid4)
    configuration_digest = models.CharField(max_length=64, default="")
    descriptor_digest = models.CharField(max_length=64)
    descriptor = models.JSONField(default=dict)
    adapter_version = models.CharField(max_length=100, default="")
    provider_config_version = models.PositiveIntegerField(null=True)
    base_commit = models.CharField(max_length=64, default="")
    tree_hash = models.CharField(max_length=64, default="")
    workspace_reference = models.CharField(max_length=200, default="")
    workspace_prepared_at = models.DateTimeField(null=True)
    runtime_session_reference = models.CharField(max_length=512, default="")
    input_cursor = models.PositiveBigIntegerField(default=0)
    event_cursor = models.PositiveBigIntegerField(default=0)
    process_state = models.CharField(
        max_length=20, choices=choices(protocol.ProcessState), default="starting"
    )
    started_at = models.DateTimeField(null=True)
    stopped_at = models.DateTimeField(null=True)
    ended_at = models.DateTimeField(null=True)
    source_checkpoint = models.ForeignKey(
        "AgentCheckpoint", on_delete=models.PROTECT, null=True, related_name="resumed_attempts"
    )
    protocol_fields = {
        "descriptor": protocol.AttemptDescriptor,
        "audience_binding": protocol.AudienceBinding | None,
    }

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["job", "number"], name="agent_attempt_number_unique"),
            models.UniqueConstraint(
                fields=["job"], condition=Q(active=True), name="agent_attempt_one_active"
            ),
            models.UniqueConstraint(
                fields=["runner", "claim_key"], name="agent_attempt_claim_unique"
            ),
            models.UniqueConstraint(
                fields=["job", "lease_epoch"], name="agent_attempt_epoch_unique"
            ),
            state_constraint("process_state", protocol.ProcessState, "agent_attempt_process_valid"),
            models.CheckConstraint(
                condition=Q(number__gte=1, lease_epoch__gte=1), name="agent_attempt_number_positive"
            ),
            models.CheckConstraint(
                condition=Q(active=True, ended_at__isnull=True)
                | Q(active=False, ended_at__isnull=False),
                name="agent_attempt_terminal_bound",
            ),
        ]
        indexes = [models.Index(fields=["runner", "active", "lease_expires_at"])]


class AgentContextRef(AgentRecord):
    job = models.ForeignKey(AgentJob, on_delete=models.PROTECT)
    kind = models.CharField(max_length=20)
    message = models.ForeignKey("zerver.Message", on_delete=models.SET_NULL, null=True)
    attachment = models.ForeignKey("zerver.Attachment", on_delete=models.SET_NULL, null=True)
    repository = models.ForeignKey(AgentRepository, on_delete=models.PROTECT, null=True)
    scope = models.JSONField(default=dict)
    validated_at = models.DateTimeField(default=timezone_now)
    protocol_fields = {"scope": protocol.ConversationScope}


class AgentOperation(AgentRecord):
    scope_binding = models.JSONField(default=dict)
    attempt = models.ForeignKey(AgentAttempt, on_delete=models.PROTECT)
    operation_id = models.UUIDField(unique=True)
    tool_class = models.CharField(max_length=40)
    argument_digest = models.CharField(max_length=64)
    arguments = models.JSONField(default=dict)
    status = models.CharField(
        max_length=30, choices=choices(protocol.OperationState), default="proposed"
    )
    version = models.PositiveIntegerField(default=1)
    started_at = models.DateTimeField(null=True)
    finished_at = models.DateTimeField(null=True)
    remote_receipt = models.JSONField(null=True, default=None)
    local_receipt = models.JSONField(null=True, default=None)
    server_receipt = models.JSONField(null=True, default=None)
    protocol_fields = {
        "arguments": protocol.OperationArguments,
        "remote_receipt": protocol.RemoteReceipt | None,
        "local_receipt": protocol.LocalOperationReceipt | None,
        "server_receipt": protocol.TeamReceipt | None,
    }

    class Meta:
        constraints = [
            state_constraint("status", protocol.OperationState, "agent_operation_state_valid")
        ]

    @override
    def clean(self) -> None:
        super().clean()
        if self.arguments["action"] != self.tool_class:
            raise ValidationError("Operation action does not match its tool class.")


class AgentApproval(AgentRecord):
    job = models.ForeignKey(AgentJob, on_delete=models.PROTECT)
    attempt = models.ForeignKey(AgentAttempt, on_delete=models.PROTECT)
    operation = models.ForeignKey(AgentOperation, on_delete=models.PROTECT)
    approver = models.ForeignKey("zerver.UserProfile", on_delete=models.PROTECT, null=True)
    operation_hash = models.CharField(max_length=64)
    policy_version = models.PositiveIntegerField()
    version = models.PositiveIntegerField(default=1)
    tree_hash = models.CharField(max_length=64)
    arguments = models.JSONField(default=dict)
    decision = models.CharField(
        max_length=20, choices=choices(protocol.ApprovalState), default="pending"
    )
    expires_at = models.DateTimeField()
    nonce = models.UUIDField(default=uuid.uuid4, unique=True)
    decided_at = models.DateTimeField(null=True)
    consumed_at = models.DateTimeField(null=True)
    protocol_fields = {"arguments": protocol.ApprovalArguments}

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["operation"],
                condition=Q(consumed_at__isnull=False),
                name="agent_approval_one_consumption",
            ),
            state_constraint("decision", protocol.ApprovalState, "agent_approval_decision_valid"),
            models.CheckConstraint(
                condition=Q(consumed_at__isnull=True)
                | Q(decision="consumed", approver__isnull=False),
                name="agent_approval_consumption_bound",
            ),
            models.CheckConstraint(
                condition=~Q(decision="consumed") | Q(consumed_at__isnull=False),
                name="agent_approval_consumed_time",
            ),
        ]


class AgentArtifact(AgentRecord):
    audience_binding = models.JSONField(null=True, default=None)
    attempt = models.ForeignKey(AgentAttempt, on_delete=models.PROTECT)
    kind = models.CharField(max_length=30)
    checksum = models.CharField(max_length=64)
    size = models.PositiveBigIntegerField()
    storage_ref = models.CharField(max_length=512)
    filename = models.CharField(max_length=255, default="artifact")
    media_type = models.CharField(max_length=100, default="application/octet-stream")
    expires_at = models.DateTimeField()
    unavailable_at = models.DateTimeField(null=True)
    # Access is inherited from the current job ACL, never a public URL.
    acl_scope = models.JSONField(default=dict)
    protocol_fields = {
        "acl_scope": protocol.ConversationScope,
        "audience_binding": protocol.AudienceBinding | None,
    }

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=Q(size__lte=protocol.MAX_ARTIFACT_BYTES), name="agent_artifact_size_limit"
            )
        ]


class AgentVerification(AgentRecord):
    operation = models.ForeignKey(AgentOperation, on_delete=models.PROTECT, null=True)
    attempt = models.ForeignKey(AgentAttempt, on_delete=models.PROTECT)
    check_id = models.CharField(max_length=80)
    command = models.JSONField()
    cwd = models.CharField(max_length=512)
    exit_code = models.IntegerField(null=True)
    started_at = models.DateTimeField()
    finished_at = models.DateTimeField()
    tree_hash = models.CharField(max_length=64)
    output_artifact = models.ForeignKey(AgentArtifact, on_delete=models.PROTECT)
    timed_out = models.BooleanField(default=False)
    protocol_fields = {"command": list[protocol.Text]}

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=Q(finished_at__gte=models.F("started_at")),
                name="agent_verification_time_order",
            ),
            models.CheckConstraint(
                condition=Q(timed_out=False) | ~Q(exit_code=0),
                name="agent_verification_timeout_fail",
            ),
        ]
        indexes = [models.Index(fields=["attempt", "check_id", "tree_hash"])]


class AgentCheckpoint(AgentRecord):
    audience_binding = models.JSONField(null=True, default=None)
    attempt = models.ForeignKey(AgentAttempt, on_delete=models.PROTECT, related_name="checkpoints")
    base_commit = models.CharField(max_length=64, default="")
    tree_hash = models.CharField(max_length=64, default="")
    summary = models.TextField()
    context_refs = models.JSONField(default=list)
    artifact_ids = models.JSONField(default=list)
    remaining_work = models.JSONField(default=list)
    next_step = models.TextField()
    adapter_session_ref = models.CharField(max_length=512, default="")
    input_cursor = models.PositiveBigIntegerField(default=0)
    protocol_fields = {
        "audience_binding": protocol.AudienceBinding | None,
        "context_refs": list[uuid.UUID],
        "artifact_ids": list[uuid.UUID],
        "remaining_work": list[protocol.Text],
    }


class AgentAuditEvent(AgentRecord):
    job = models.ForeignKey(AgentJob, on_delete=models.PROTECT)
    attempt = models.ForeignKey(AgentAttempt, on_delete=models.PROTECT, null=True)
    event_id = models.UUIDField(default=uuid.uuid4)
    sequence = models.PositiveBigIntegerField()
    attempt_sequence = models.PositiveBigIntegerField(null=True)
    actor = models.ForeignKey("zerver.UserProfile", on_delete=models.PROTECT, null=True)
    authority = models.CharField(max_length=20)
    type = models.CharField(max_length=80)
    occurred_at = models.DateTimeField(default=timezone_now)
    payload = models.JSONField(default=dict)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["attempt", "event_id"], name="agent_event_attempt_id_unique"
            ),
            models.UniqueConstraint(fields=["job", "event_id"], name="agent_event_job_id_unique"),
            models.UniqueConstraint(fields=["job", "sequence"], name="agent_event_sequence_unique"),
            models.UniqueConstraint(
                fields=["attempt", "attempt_sequence"],
                condition=Q(attempt_sequence__isnull=False),
                name="agent_event_attempt_seq_unique",
            ),
            models.CheckConstraint(
                condition=Q(sequence__gte=1), name="agent_event_sequence_positive"
            ),
            models.CheckConstraint(
                condition=Q(authority__in=["server", "runner", "verifier", "publisher"]),
                name="agent_event_authority_valid",
            ),
        ]
        indexes = [models.Index(fields=["job", "sequence"])]

    @override
    def clean(self) -> None:
        super().clean()
        envelope = {
            "schema_version": 1,
            "job_id": str(self.job_id),
            "attempt_id": str(self.attempt_id) if self.attempt_id else None,
            "lease_epoch": self.attempt.lease_epoch if self.attempt is not None else None,
            "event_id": str(self.event_id),
            "sequence": self.attempt_sequence or self.sequence,
            "type": self.type,
            "occurred_at": self.occurred_at.isoformat(),
            "payload": self.payload,
        }
        try:
            protocol.parse_authority_event(self.authority, envelope)
        except (ProtocolValidationError, ValueError):
            raise ValidationError("Invalid agent event or authority.") from None


class AgentOutbox(AgentRecord):
    job = models.ForeignKey(AgentJob, on_delete=models.PROTECT, null=True)
    delivery_key = models.CharField(max_length=200, unique=True)
    event_type = models.CharField(max_length=80)
    payload_ref = models.UUIDField(null=True)
    attempt_count = models.PositiveIntegerField(default=0)
    next_attempt_at = models.DateTimeField(default=timezone_now)
    status = models.CharField(max_length=20, default="pending")
    delivered_at = models.DateTimeField(null=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=Q(status__in=["pending", "processing", "delivered", "blocked"]),
                name="agent_outbox_state_valid",
            )
        ]
        indexes = [models.Index(fields=["realm", "status", "next_attempt_at"])]


class AgentSetupOperation(AgentRecord):
    owner = models.ForeignKey("zerver.UserProfile", on_delete=models.PROTECT)
    profile = models.ForeignKey(AgentProfile, on_delete=models.PROTECT, null=True)
    provider = models.ForeignKey(AgentProvider, on_delete=models.PROTECT, null=True)
    runner = models.ForeignKey(AgentRunner, on_delete=models.PROTECT)
    profile_revision = models.PositiveIntegerField(default=1)
    provider_config_version = models.PositiveIntegerField(null=True)
    configuration_digest = models.CharField(max_length=64, default="")
    descriptor_digest = models.CharField(max_length=64, default="")
    descriptor = models.JSONField(default=dict)
    phase = models.CharField(max_length=30, default="pending")
    result = models.JSONField(default=None, null=True)
    requirements = models.JSONField(default=list)
    retry_key = models.UUIDField()
    payload_digest = models.CharField(max_length=64)
    claim_key = models.UUIDField(null=True)
    lease_epoch = models.PositiveBigIntegerField(default=0)
    lease_expires_at = models.DateTimeField(null=True)
    finished_at = models.DateTimeField(null=True)
    protocol_fields = {
        "descriptor": protocol.ProbeDescriptor,
        "requirements": list[protocol.Requirement],
    }

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["realm", "owner", "retry_key"], name="agent_setup_idempotency_unique"
            ),
            models.UniqueConstraint(
                fields=["runner", "claim_key"],
                condition=Q(claim_key__isnull=False),
                name="agent_setup_claim_unique",
            ),
            models.CheckConstraint(
                condition=Q(
                    phase__in=[
                        "pending",
                        "claimed",
                        "probing",
                        "ready",
                        "needs_action",
                        "failed",
                        "stale",
                        "cancelled",
                    ]
                ),
                name="agent_setup_phase_valid",
            ),
        ]
        indexes = [models.Index(fields=["realm", "runner", "phase"])]


class AgentProbeGrant(AgentRecord):
    setup_operation = models.ForeignKey(AgentSetupOperation, on_delete=models.PROTECT)
    runner = models.ForeignKey(AgentRunner, on_delete=models.PROTECT)
    provider = models.ForeignKey(AgentProvider, on_delete=models.PROTECT, null=True)
    profile_revision = models.PositiveIntegerField()
    provider_config_version = models.PositiveIntegerField(null=True)
    token_hash = models.CharField(max_length=128, unique=True)
    expires_at = models.DateTimeField()
    revoked_at = models.DateTimeField(null=True)


class AgentSendIntent(AgentRecord):
    identity_unavailable = models.BooleanField(default=False)
    # This identity survives deletion of the nullable source message.
    sent_message_id = models.PositiveBigIntegerField(null=True)
    sender = models.ForeignKey("zerver.UserProfile", on_delete=models.PROTECT)
    client_key = models.UUIDField()
    payload_digest = models.CharField(max_length=64)
    source_message = models.OneToOneField("zerver.Message", on_delete=models.SET_NULL, null=True)
    expires_at = models.DateTimeField(null=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["realm", "sender", "client_key"], name="agent_send_sender_key_unique"
            )
        ]


class AgentDispatchReceipt(AgentRecord):
    source_message = models.ForeignKey("zerver.Message", on_delete=models.SET_NULL, null=True)
    profile = models.ForeignKey(AgentProfile, on_delete=models.PROTECT)
    requester = models.ForeignKey("zerver.UserProfile", on_delete=models.PROTECT)
    trigger_kind = models.CharField(max_length=30, choices=choices(protocol.TriggerKind))
    decision = models.CharField(max_length=20)
    reason = models.CharField(max_length=200, default="")
    job = models.ForeignKey(AgentJob, on_delete=models.PROTECT, null=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["realm", "source_message", "profile", "trigger_kind"],
                name="agent_dispatch_trigger_unique",
            ),
            models.CheckConstraint(
                condition=Q(decision="rejected", job__isnull=True)
                | Q(decision__in=["accepted", "needs_input"], job__isnull=False),
                name="agent_dispatch_job_decision",
            ),
        ]


class AgentInput(AgentRecord):
    reconciliation_receipt = models.JSONField(null=True, default=None)
    job = models.ForeignKey(AgentJob, on_delete=models.PROTECT)
    author = models.ForeignKey("zerver.UserProfile", on_delete=models.PROTECT)
    source_message = models.ForeignKey("zerver.Message", on_delete=models.SET_NULL, null=True)
    client_key = models.UUIDField()
    payload_digest = models.CharField(max_length=64)
    sequence = models.PositiveBigIntegerField()
    input_type = models.CharField(max_length=20, default="steering")
    text = models.TextField()
    delivery_state = models.CharField(
        max_length=30, choices=choices(protocol.InputState), default="pending"
    )
    delivered_attempt = models.ForeignKey(AgentAttempt, on_delete=models.PROTECT, null=True)
    applied_at = models.DateTimeField(null=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["job", "client_key"], name="agent_input_client_key_unique"
            ),
            models.UniqueConstraint(
                fields=["job", "source_message"],
                condition=Q(source_message__isnull=False),
                name="agent_input_source_unique",
            ),
            models.UniqueConstraint(fields=["job", "sequence"], name="agent_input_sequence_unique"),
            state_constraint("delivery_state", protocol.InputState, "agent_input_delivery_valid"),
            models.CheckConstraint(
                condition=Q(input_type__in=["steering", "answer", "replan"]),
                name="agent_input_type_valid",
            ),
            models.CheckConstraint(
                condition=Q(sequence__gte=1), name="agent_input_sequence_positive"
            ),
        ]
