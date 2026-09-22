"""Synthetic Task 9 browser fixture for the dedicated test database."""

import json
import os
import sys
from datetime import timedelta
from uuid import uuid4

sys.path.insert(0, os.getcwd())
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "zproject.test_settings")

import django

django.setup()

from django.utils.timezone import now

from zerver.actions import agent_jobs
from zerver.actions.agents import create_profile, current_execution_configuration
from zerver.actions.message_send import internal_send_private_message
from zerver.lib import agent_protocol as protocol
from zerver.lib.agent_context import current_audience, scope_for_message
from zerver.lib.streams import filter_stream_authorization_for_adding_subscribers
from zerver.models import Message, Recipient, Stream, Subscription, UserProfile, agents

owner = UserProfile.objects.get(delivery_email=sys.argv[1])
realm = owner.realm
attachment_stream = None
for subscription in (
    Subscription.objects.filter(user_profile=owner, active=True, recipient__type=Recipient.STREAM)
    .select_related("recipient")
    .order_by("recipient_id")
):
    stream = Stream.objects.get(id=subscription.recipient.type_id)
    if filter_stream_authorization_for_adding_subscribers(
        owner, [stream], True
    ).authorized_streams == [stream]:
        attachment_stream = stream
        break
assert attachment_stream is not None
settings, _ = agents.AgentRealmSettings.objects.update_or_create(
    realm=realm, defaults={"enabled": True}
)
runner = agents.AgentRunner.objects.create(
    realm=realm,
    owner=owner,
    name="Browser test workstation",
    host_kind="workstation",
    fingerprint=uuid4().hex * 2,
    catalog_report={
        "revision": 1,
        "adapters": [
            {
                "id": "acp",
                "version": "1",
                "auth_state": "ready",
                "capabilities": {"config_version": 1},
            }
        ],
        "sandboxes": [
            {
                "alias": "default",
                "image_digest": "sha256:" + "a" * 64,
                "toolchain_digest": "b" * 64,
                "catalog_revision": 1,
                "cpu_millicores": 100,
                "memory_bytes": 67108864,
                "pids_limit": 16,
                "temporary_bytes": 1048576,
            }
        ],
    },
)
runner.catalog_revision = 1
runner.save(update_fields=["catalog_revision"])
provider = agents.AgentProvider.objects.create(
    realm=realm,
    owner=owner,
    runner=runner,
    name="Synthetic local model",
    base_url="http://wulan.test:11434",
    model_id="synthetic-model",
    allowed_models=["synthetic-model"],
    context_window_tokens=8192,
    max_output_tokens=1024,
    local_credential_ref="test-local-ref",
    data_scope=["synthetic", "selected_chat", "selected_repository"],
    network_policy={
        "targets": [
            {
                "hostname": "wulan.test",
                "port": 11434,
                "allow_private": True,
                "allow_http_loopback": False,
                "allow_http_private": True,
            }
        ],
        "public_https_only": True,
        "block_metadata": True,
        "cross_origin_authorization": False,
        "project_network": False,
    },
    capability_report={"config_version": 1},
)
profile = create_profile(
    owner,
    name="Duplicate profile name",
    runner=runner,
    adapter_id="acp",
    adapter_version="1",
    provider=provider,
    idempotency_key=uuid4(),
)
second = create_profile(
    owner,
    name="Duplicate profile name",
    runner=runner,
    adapter_id="acp",
    adapter_version="1",
    idempotency_key=uuid4(),
)
second_setup = (
    agents.AgentSetupOperation.objects.filter(profile=second).order_by("-created_at").first()
)
assert second_setup is not None
second_setup.phase = "needs_action"
second_setup.requirements = [
    {
        "code": "runner_offline",
        "surface": "runner",
        "action": "connect_runner",
        "diagnostic_id": None,
    }
]
second_setup.save(update_fields=["phase", "requirements"])
conversation = agents.AgentConversation.objects.create(
    realm=realm,
    profile=profile,
    scope={"kind": "direct", "participant_user_ids": [owner.id]},
)
job = agents.AgentJob.objects.create(
    realm=realm,
    requester=owner,
    conversation=conversation,
    profile=profile,
    runner=runner,
    request="Synthetic UI check",
    idempotency_key=uuid4(),
    payload_digest="c" * 64,
    policy=profile.policy,
    budget=profile.budget,
    status="blocked",
    blocked_reason="synthetic_test_fixture",
)
repository = agents.AgentRepository.objects.create(
    realm=realm,
    owner=owner,
    runner=runner,
    workspace_alias="evidence",
    allowed_refs=["main"],
    required_checks=[{"id": "unit", "argv": ["true"], "cwd": ".", "timeout_seconds": 120}],
)
message_id = internal_send_private_message(owner, profile.bot_user, "Synthetic evidence task")
assert message_id is not None
message = Message.objects.get(id=message_id)
task_source_message_id = internal_send_private_message(
    owner, profile.bot_user, "Independent task source"
)
assert task_source_message_id is not None
evidence_conversation = agents.AgentConversation.objects.create(
    realm=realm,
    profile=profile,
    repository=repository,
    anchor_message=message,
    scope=protocol.serialize_payload(scope_for_message(message)),
)
evidence_conversation.audience_binding = protocol.serialize_payload(
    current_audience(evidence_conversation, owner, profile.bot_user)
)
evidence_conversation.save(update_fields=["audience_binding"])
profile.default_repository = repository
profile.policy = {
    **profile.policy,
    "scope": protocol.serialize_payload(scope_for_message(message)),
    "actions": ["context.read", "repository.read", "checks.run"],
}
profile.desired_state = "enabled"
profile.readiness_state = "ready"
profile.enabled_revision = profile.revision
profile.readiness_revision = profile.revision
profile.capability_report = {
    "chat_ready": True,
    "code_ready": True,
    "tool_calling": "passed",
    "sandbox": "passed",
    "config_version": 1,
}
tested = current_execution_configuration(profile)
profile.readiness_configuration = protocol.serialize_payload(tested)
profile.readiness_configuration_digest = agent_jobs.digest(profile.readiness_configuration)
profile.save(
    update_fields=[
        "default_repository",
        "policy",
        "desired_state",
        "readiness_state",
        "enabled_revision",
        "readiness_revision",
        "capability_report",
        "readiness_configuration",
        "readiness_configuration_digest",
    ]
)
evidence_job = agents.AgentJob.objects.create(
    realm=realm,
    requester=owner,
    conversation=evidence_conversation,
    profile=profile,
    runner=runner,
    repository=repository,
    source_message=message,
    request="Synthetic evidence task",
    idempotency_key=uuid4(),
    payload_digest="d" * 64,
    policy=profile.policy,
    budget=profile.budget,
    status="waiting_for_approval",
    phase="verify",
    version=2,
    job_kind="code",
    delivery_target="patch",
    base_ref="main",
    input_sequence=1,
)
attempt = agents.AgentAttempt(
    realm=realm,
    job=evidence_job,
    runner=runner,
    number=1,
    lease_epoch=1,
    lease_expires_at=now() + timedelta(minutes=5),
    audience_binding=evidence_conversation.audience_binding,
    process_state="active",
    tree_hash="e" * 40,
    base_commit="a" * 40,
)
descriptor = agent_jobs.build_descriptor(evidence_job, attempt)
attempt.descriptor = descriptor
attempt.descriptor_digest = descriptor["descriptor_digest"]
attempt.configuration_digest = descriptor["configuration_digest"]
attempt.save()
operation = agents.AgentOperation.objects.create(
    realm=realm,
    attempt=attempt,
    operation_id=uuid4(),
    tool_class="checks.run",
    argument_digest="c" * 64,
    arguments={
        "action": "checks.run",
        "repository_id": str(repository.id),
        "check_ids": ["unit"],
        "tree_hash": attempt.tree_hash,
    },
    scope_binding={
        "attempt_id": str(attempt.id),
        "lease_epoch": attempt.lease_epoch,
        "policy_version": profile.policy_version,
        "tree_hash": attempt.tree_hash,
    },
    status="proposed",
)
agents.AgentApproval.objects.create(
    realm=realm,
    job=evidence_job,
    attempt=attempt,
    operation=operation,
    operation_hash=operation.argument_digest,
    policy_version=profile.policy_version,
    tree_hash=attempt.tree_hash,
    arguments=operation.arguments,
    expires_at=now() + timedelta(minutes=5),
)
artifact = agents.AgentArtifact.objects.create(
    realm=realm,
    attempt=attempt,
    kind="diff",
    checksum="a" * 64,
    size=1,
    storage_ref=f"artifacts/{uuid4()}",
    filename="evidence.patch",
    media_type="text/plain",
    expires_at=now() + timedelta(days=1),
    acl_scope=protocol.serialize_payload(scope_for_message(message)),
    audience_binding=evidence_conversation.audience_binding,
)
agents.AgentVerification.objects.create(
    realm=realm,
    attempt=attempt,
    check_id="unit",
    command=["true"],
    cwd=".",
    exit_code=0,
    started_at=now(),
    finished_at=now(),
    tree_hash=attempt.tree_hash,
    output_artifact=artifact,
)
agents.AgentInput.objects.create(
    realm=realm,
    job=evidence_job,
    author=owner,
    client_key=uuid4(),
    payload_digest="f" * 64,
    sequence=1,
    input_type="steering",
    text="Keep the unit check visible.",
    delivery_state="delivered",
    delivered_attempt=attempt,
)
agents.AgentAuditEvent.objects.create(
    realm=realm,
    job=evidence_job,
    attempt=attempt,
    sequence=1,
    event_id=uuid4(),
    authority="server",
    type="attempt.started",
    payload={},
)
print(
    json.dumps(
        {
            "job_id": str(job.id),
            "evidence_job_id": str(evidence_job.id),
            "provider_id": str(provider.id),
            "owner_id": owner.id,
            "profile_ids": [str(profile.id), str(second.id)],
            "source_message_id": task_source_message_id,
            "bot_user_id": profile.bot_user_id,
            "attachment_channel_id": attachment_stream.id,
        }
    )
)
