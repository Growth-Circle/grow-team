"""Closed request contracts for lifecycle HTTP boundaries."""

from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field

from zerver.lib import agent_protocol as p


class Request(p.Versioned):
    schema_version: Literal[1]


class CreateJob(Request):
    profile_id: UUID
    source_message_id: p.Positive
    repository_id: UUID | None = None
    request: Annotated[str, Field(min_length=1, max_length=20000)]
    idempotency_key: UUID
    job_kind: p.JobKind = "answer"
    delivery_target: p.DeliveryTarget = "answer"
    base_ref: Annotated[str, Field(max_length=200)] = ""
    context_message_ids: list[p.Positive] = Field(default_factory=list, max_length=100)
    context_attachment_ids: list[p.Positive] = Field(default_factory=list, max_length=20)


class MessagePreflight(Request):
    profile_ids: list[UUID] = Field(min_length=1, max_length=20)
    source_message_id: p.Positive | None = None


class JobControl(Request):
    expected_version: p.Positive


class Resume(JobControl):
    checkpoint_id: UUID | None = None


class Input(JobControl):
    client_key: UUID
    text: Annotated[str, Field(min_length=1, max_length=20000)]
    input_type: Literal["steering", "answer", "replan"] = "steering"
    source_message_id: p.Positive | None = None


class ApprovalDecision(JobControl):
    operation_hash: p.Digest
    nonce: UUID
    decision: Literal["approved", "rejected"]


class LeaseRequest(p.LeaseIdentity, Request):
    job_version: p.Positive


class EventBatch(Request):
    events: list[p.RunnerEvent] = Field(min_length=1, max_length=100)
    job_version: p.Positive


class Heartbeat(Request):
    leases: list[p.LeaseIdentity] = Field(default_factory=list, max_length=100)


class Context(LeaseRequest):
    reference_ids: list[UUID] = Field(min_length=1, max_length=100)


class ContextFile(LeaseRequest):
    reference_id: UUID


class Artifact(LeaseRequest):
    checksum: p.Digest
    kind: Literal["diff", "summary", "verification", "log", "file"]
    filename: Annotated[str, Field(min_length=1, max_length=255)]
    media_type: Literal["text/plain", "text/x-diff", "application/json", "application/octet-stream"]


class Proposal(LeaseRequest):
    operation_id: UUID
    arguments: p.OperationArguments
    tree_hash: p.GitHash | None = None
    diff_artifact_id: UUID | None = None


class Consume(LeaseRequest):
    operation_id: UUID
    expected_version: p.Positive
    operation_hash: p.Digest
    nonce: UUID | None = None


class Reconcile(LeaseRequest):
    operation_id: UUID
    receipt: p.RemoteReceipt


class ReconcileLocal(LeaseRequest):
    receipt: p.LocalOperationReceipt


class Checkpoint(LeaseRequest):
    checkpoint: p.Checkpoint


class Credential(LeaseRequest):
    provider_id: UUID
    secret_version: p.Positive


class ProbeCredential(Request):
    setup_id: UUID
    claim_key: UUID
    lease_epoch: p.Positive
    descriptor_digest: p.Digest
    configuration_digest: p.Digest
    provider_id: UUID
    secret_version: p.Positive


class StopEvidence(Request):
    event: p.RunnerEvent


class InputReconciliation(LeaseRequest):
    input_id: UUID
    input_sequence: p.Positive
    outcome: Literal["applied", "not_applied"]
    receipt_id: UUID


class Claim(p.ClaimRequest):
    schema_version: Literal[1]
