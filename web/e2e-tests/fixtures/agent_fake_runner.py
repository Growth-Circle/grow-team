"""Fake runner CLI for the browser e2e suite (contract 15.2).

Run: python3 web/e2e-tests/fixtures/agent_fake_runner.py <command> [options]

Each command calls the same Django actions the real runner HTTP endpoints
call, without the wire layer, so a Puppeteer test can drive one job through
its full lifecycle inside the dedicated test database. The CLI prints one
JSON object on stdout and exits 0 on success. On failure it prints
{"error": str(exc)} and exits 1.
"""

import argparse
import hashlib
import json
import os
import sys
from datetime import timedelta
from pathlib import Path
from uuid import UUID, uuid4

sys.path.insert(0, os.getcwd())
os.environ.setdefault("PUPPETEER_TESTS", "1")
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "zproject.test_settings")

import django

django.setup()

from django.conf import settings
from django.utils.timezone import now

from zerver.actions import agent_approvals as approvals
from zerver.actions import agent_jobs as jobs
from zerver.actions.agents import (
    claim_setup,
    create_profile,
    enable_profile,
    record_setup_result,
    share_agent_profile,
)
from zerver.actions.message_send import internal_send_stream_message
from zerver.actions.streams import bulk_add_subscriptions
from zerver.lib import agent_protocol as p
from zerver.lib.agent_reconcile import reconcile_agents
from zerver.lib.agent_requests import SetupResult
from zerver.lib.agent_results import store_artifact
from zerver.lib.streams import create_stream_if_needed
from zerver.models import Recipient, UserProfile, agents

MEMBER_PASSWORD = "fake-e2e-member-password"


def _catalog() -> dict[str, object]:
    return {
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
    }


def _active_attempt(job: "agents.AgentJob") -> "agents.AgentAttempt":
    return agents.AgentAttempt.objects.get(job=job, active=True)


def _emit(
    runner: "agents.AgentRunner",
    job: "agents.AgentJob",
    attempt: "agents.AgentAttempt",
    event_type: str,
    payload: dict[str, object],
) -> dict[str, object]:
    """Build and record one runner event with the next sequence for this attempt."""
    attempt.refresh_from_db()
    event = p.RunnerEvent.model_validate(
        {
            "schema_version": 1,
            "job_id": str(job.id),
            "attempt_id": str(attempt.id),
            "lease_epoch": attempt.lease_epoch,
            "event_id": str(uuid4()),
            "sequence": attempt.event_cursor + 1,
            "type": event_type,
            "occurred_at": now().isoformat(),
            "payload": payload,
        }
    )
    return jobs.record_event(runner, event)


def _pass_probe(
    runner: "agents.AgentRunner",
    profile: "agents.AgentProfile",
    *,
    ready: bool,
    code: str | None = None,
    surface: str | None = None,
    action: str | None = None,
) -> "agents.AgentProfile":
    """Claim the profile's newest pending setup, then record its result."""
    setup = (
        agents.AgentSetupOperation.objects.filter(
            profile=profile, phase="pending", profile_revision=profile.revision
        )
        .order_by("-created_at")
        .first()
    )
    assert setup is not None, "The profile has no pending setup at its current revision."
    claimed = claim_setup(runner, setup.id, uuid4())
    config_version = claimed.provider_config_version or profile.revision
    requirements = (
        [{"code": code, "surface": surface, "action": action, "diagnostic_id": None}]
        if code
        else []
    )
    result = SetupResult(
        schema_version=1,
        setup_id=claimed.id,
        claim_key=claimed.claim_key,
        lease_epoch=claimed.lease_epoch,
        descriptor_digest=claimed.descriptor_digest,
        configuration_digest=claimed.configuration_digest,
        state="ready" if ready else "needs_action",
        capabilities={
            "chat_ready": True,
            "code_ready": True,
            "tool_calling": "passed",
            "team_tools": "passed",
            "sandbox": "passed",
            "config_version": config_version,
        },
        requirements=requirements,
    )
    record_setup_result(runner, result)
    profile.refresh_from_db()
    return profile


def _ensure_artifact_root() -> None:
    """Create the private artifact directory at mode 0700 (contract 15.1)."""
    root = Path(settings.AGENT_ARTIFACT_ROOT)
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(root, 0o700)


def cmd_scenario(args: argparse.Namespace) -> None:
    owner = UserProfile.objects.get(delivery_email=args.owner)
    realm = owner.realm
    if args.member:
        member = UserProfile.objects.get(delivery_email=args.member, realm=realm)
    else:
        member = (
            UserProfile.objects.filter(realm=realm, is_bot=False, is_active=True)
            .exclude(id=owner.id)
            .exclude(delivery_email="")
            .order_by("id")
            .first()
        )
    assert member is not None, "The realm needs a second human member for the member share."
    agents.AgentRealmSettings.objects.update_or_create(realm=realm, defaults={"enabled": True})
    runner = agents.AgentRunner.objects.create(
        realm=realm,
        owner=owner,
        name="Fake e2e runner",
        host_kind="server",
        fingerprint=uuid4().hex * 2,
        catalog_report=_catalog(),
        catalog_revision=1,
    )
    provider = agents.AgentProvider.objects.create(
        realm=realm,
        owner=owner,
        runner=runner,
        name="Fake e2e model",
        base_url="http://wulan.test:11434",
        model_id="fake-e2e-model",
        allowed_models=["fake-e2e-model"],
        context_window_tokens=8192,
        max_output_tokens=1024,
        local_credential_ref="fake-e2e-ref",
        data_scope=["synthetic"],
        capability_report={"config_version": 1},
    )
    profile = create_profile(
        owner,
        name=f"Fake e2e agent {uuid4().hex[:8]}",
        runner=runner,
        adapter_id="acp",
        adapter_version="1",
        provider=provider,
        default_mode=args.kind,
        idempotency_key=uuid4(),
    )
    profile = _pass_probe(runner, profile, ready=True)
    if args.state == "enabled":
        enable_profile(owner, profile, expected_revision=profile.revision)
        profile.refresh_from_db()
    stream, _created = create_stream_if_needed(
        realm, f"fake-e2e-{uuid4().hex[:8]}", acting_user=owner
    )
    bulk_add_subscriptions(realm, [stream], [owner, member, profile.bot_user], acting_user=owner)
    member.set_password(MEMBER_PASSWORD)
    member.save(update_fields=["password"])
    share_agent_profile(owner, profile, principal_user=member, allow_job_control=True)
    topic = "fake e2e scenario"
    source_message_id = internal_send_stream_message(
        owner, stream, topic, "Fixture source message for the browser e2e suite."
    )
    print(
        json.dumps(
            {
                "runner_id": str(runner.id),
                "profile_id": str(profile.id),
                "bot_user_id": profile.bot_user_id,
                "stream_id": stream.id,
                "topic": topic,
                "source_message_id": source_message_id,
                "member_email": member.delivery_email,
                "member_password": MEMBER_PASSWORD,
            }
        )
    )


def cmd_heartbeat(args: argparse.Namespace) -> None:
    runner = agents.AgentRunner.objects.get(id=UUID(args.runner))
    identities = []
    if args.job:
        job = agents.AgentJob.objects.get(id=UUID(args.job))
        attempt = _active_attempt(job)
        identities.append(
            p.LeaseIdentity(job_id=job.id, attempt_id=attempt.id, lease_epoch=attempt.lease_epoch)
        )
    jobs.heartbeat(runner, identities)
    runner.refresh_from_db()
    print(json.dumps({"status": runner.status}))


def cmd_probe(args: argparse.Namespace) -> None:
    profile = agents.AgentProfile.objects.get(id=UUID(args.profile))
    profile = _pass_probe(
        profile.runner,
        profile,
        ready=args.result == "ready",
        code=args.code,
        surface=args.surface,
        action=args.action,
    )
    print(json.dumps({"readiness_state": profile.readiness_state}))


def cmd_claim(args: argparse.Namespace) -> None:
    runner = agents.AgentRunner.objects.get(id=UUID(args.runner))
    descriptor = jobs.claim_work(runner, claim_key=uuid4())
    if descriptor is None:
        print(json.dumps({"job_id": None}))
        return
    print(
        json.dumps(
            {
                "job_id": descriptor["job_id"],
                "attempt_id": descriptor["attempt_id"],
                "lease_epoch": descriptor["lease_epoch"],
            }
        )
    )


def cmd_start(args: argparse.Namespace) -> None:
    job = agents.AgentJob.objects.get(id=UUID(args.job))
    attempt = _active_attempt(job)
    _emit(job.runner, job, attempt, "attempt.starting", {"process_state": "starting"})
    receipt = _emit(job.runner, job, attempt, "attempt.started", {"process_state": "active"})
    print(json.dumps({"status": receipt["status"]}))


def cmd_approval(args: argparse.Namespace) -> None:
    """Propose a team.manage channel.unsubscribe step for the scenario member."""
    job = agents.AgentJob.objects.select_related("source_message", "profile").get(id=UUID(args.job))
    attempt = _active_attempt(job)
    assert job.source_message is not None, "The job needs a channel source message."
    channel_id = job.source_message.recipient.type_id
    # "The member" is the scenario's shared, non-owner user, regardless of who
    # sent the mention that created this job.
    member = (
        UserProfile.objects.filter(
            realm=job.realm,
            is_bot=False,
            subscription__recipient__type=Recipient.STREAM,
            subscription__recipient__type_id=channel_id,
            subscription__active=True,
        )
        .exclude(id=job.profile.owner_id)
        .order_by("id")
        .first()
    )
    assert member is not None, "The scenario channel needs a member other than the owner."
    operation = approvals.propose_operation(
        job.runner,
        job.id,
        attempt.id,
        attempt.lease_epoch,
        operation_id=uuid4(),
        arguments={
            "action": "team.manage",
            "input": {
                "tool": "channel.unsubscribe",
                "channel_id": channel_id,
                "user_ids": [member.id],
            },
        },
        tree_hash=None,
        diff_artifact_id=None,
    )
    approval = agents.AgentApproval.objects.get(operation=operation)
    print(
        json.dumps({"operation_id": str(operation.operation_id), "approval_id": str(approval.id)})
    )


def cmd_execute(args: argparse.Namespace) -> None:
    job = agents.AgentJob.objects.get(id=UUID(args.job))
    attempt = _active_attempt(job)
    operation = agents.AgentOperation.objects.get(operation_id=UUID(args.operation), attempt=attempt)
    approval = agents.AgentApproval.objects.filter(operation=operation, decision="approved").first()
    job.refresh_from_db()
    operation.refresh_from_db()
    result = approvals.execute_operation(
        job.runner,
        job.id,
        attempt.id,
        attempt.lease_epoch,
        job_version=job.version,
        operation_id=operation.operation_id,
        expected_version=operation.version,
        operation_hash=operation.argument_digest,
        nonce=approval.nonce if approval else None,
    )
    print(json.dumps({"server_receipt": result["server_receipt"]}))


def cmd_finish(args: argparse.Namespace) -> None:
    job = agents.AgentJob.objects.get(id=UUID(args.job))
    attempt = _active_attempt(job)
    _ensure_artifact_root()
    content = args.summary.encode()
    artifact = store_artifact(
        job.runner,
        job.id,
        attempt.id,
        attempt.lease_epoch,
        chunks=[content],
        checksum=hashlib.sha256(content).hexdigest(),
        kind="summary",
        filename="result.txt",
        media_type="text/plain",
    )
    _emit(
        job.runner,
        job,
        attempt,
        "result.prepared",
        {"artifact_ids": [str(artifact.id)], "summary": args.summary},
    )
    job.refresh_from_db()
    attempt.refresh_from_db()
    jobs.heartbeat(
        job.runner,
        [p.LeaseIdentity(job_id=job.id, attempt_id=attempt.id, lease_epoch=attempt.lease_epoch)],
    )
    _emit(
        job.runner, job, attempt, "attempt.stopped", {"process_state": "stopped", "stop_confirmed": True}
    )
    job.refresh_from_db()
    print(json.dumps({"status": job.status}))


def cmd_stop(args: argparse.Namespace) -> None:
    job = agents.AgentJob.objects.get(id=UUID(args.job))
    attempt = _active_attempt(job)
    attempt.refresh_from_db()
    assert attempt.process_state == "stopping", "The attempt is not stopping yet."
    _emit(
        job.runner, job, attempt, "attempt.stopped", {"process_state": "stopped", "stop_confirmed": True}
    )
    job.refresh_from_db()
    print(json.dumps({"status": job.status}))


def cmd_lose_lease(args: argparse.Namespace) -> None:
    job = agents.AgentJob.objects.get(id=UUID(args.job))
    attempt = _active_attempt(job)
    attempt.lease_expires_at = now() - timedelta(minutes=1)
    attempt.save(update_fields=["lease_expires_at"])
    reconcile_agents(limit=10)
    job.refresh_from_db()
    print(json.dumps({"status": job.status, "reason_code": job.blocked_reason}))


def cmd_ack_inputs(args: argparse.Namespace) -> None:
    job = agents.AgentJob.objects.get(id=UUID(args.job))
    attempt = _active_attempt(job)
    delivered = jobs.deliver_inputs(job.runner, job.id, attempt.id, attempt.lease_epoch)
    for item in delivered:
        _emit(
            job.runner,
            job,
            attempt,
            "input.applied",
            {
                "input_id": item["id"],
                "input_sequence": item["sequence"],
                "delivery_state": "applied",
            },
        )
    print(json.dumps({"count": len(delivered)}))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    scenario = sub.add_parser("scenario", help="Build a realm, runner, profile, and channel.")
    scenario.add_argument("--owner", required=True)
    scenario.add_argument("--member")
    scenario.add_argument("--kind", choices=["answer", "manage"], default="answer")
    scenario.add_argument("--state", choices=["enabled", "draft"], default="enabled")

    heartbeat = sub.add_parser("heartbeat", help="Mark a runner online, with an optional lease.")
    heartbeat.add_argument("--runner", required=True)
    heartbeat.add_argument("--job")

    probe = sub.add_parser("probe", help="Complete a profile's newest pending setup.")
    probe.add_argument("--profile", required=True)
    probe.add_argument("--result", choices=["ready", "needs_action"], required=True)
    probe.add_argument("--code")
    probe.add_argument("--surface")
    probe.add_argument("--action")

    claim = sub.add_parser("claim", help="Claim the next queued job for a runner.")
    claim.add_argument("--runner", required=True)

    start = sub.add_parser("start", help="Report attempt.starting and attempt.started.")
    start.add_argument("--job", required=True)

    approval = sub.add_parser("approval", help="Propose a team.manage step that needs approval.")
    approval.add_argument("--job", required=True)

    execute = sub.add_parser("execute", help="Execute an approved team.manage operation.")
    execute.add_argument("--job", required=True)
    execute.add_argument("--operation", required=True)

    finish = sub.add_parser("finish", help="Store the result and stop the attempt.")
    finish.add_argument("--job", required=True)
    finish.add_argument("--summary", required=True)

    stop = sub.add_parser("stop", help="Confirm a stop for a stopping attempt.")
    stop.add_argument("--job", required=True)

    lose_lease = sub.add_parser("lose-lease", help="Expire the lease and reconcile it.")
    lose_lease.add_argument("--job", required=True)

    ack_inputs = sub.add_parser("ack-inputs", help="Deliver pending input and confirm it.")
    ack_inputs.add_argument("--job", required=True)

    return parser


COMMANDS = {
    "scenario": cmd_scenario,
    "heartbeat": cmd_heartbeat,
    "probe": cmd_probe,
    "claim": cmd_claim,
    "start": cmd_start,
    "approval": cmd_approval,
    "execute": cmd_execute,
    "finish": cmd_finish,
    "stop": cmd_stop,
    "lose-lease": cmd_lose_lease,
    "ack-inputs": cmd_ack_inputs,
}


def main() -> None:
    args = build_parser().parse_args()
    COMMANDS[args.command](args)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # A test fixture reports every failure the same way.
        print(json.dumps({"error": str(exc)}))
        sys.exit(1)
