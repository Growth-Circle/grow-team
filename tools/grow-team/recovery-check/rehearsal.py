#!/usr/bin/env python3
"""Create and verify one isolated nontrivial agent recovery rehearsal."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

import django

V1_CANARY = "recovery-v1-synthetic-canary"
V2_CANARY = "recovery-v2-synthetic-canary"


def private_directory(path: Path) -> None:
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(path, 0o700)


def private_file(path: Path, content: bytes) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as output:
        output.write(content)
        output.flush()
        os.fsync(output.fileno())


def state_directory() -> Path:
    path = Path(os.environ["GROW_TEAM_RECOVERY_STATE"])
    private_directory(path)
    return path


def keyring_path() -> Path:
    return state_directory() / "source-private" / "keyring.json"


def artifact_root() -> Path:
    return state_directory() / "source-private" / "artifacts"


def write_keyring(current: str, keys: dict[str, bytes]) -> None:
    path = keyring_path()
    private_directory(path.parent)
    payload = {
        "current": current,
        "keys": {key_id: base64.b64encode(value).decode("ascii") for key_id, value in keys.items()},
    }
    temporary = path.with_name("keyring.json.partial")
    private_file(temporary, (json.dumps(payload, sort_keys=True) + "\n").encode())
    os.replace(temporary, path)
    os.chmod(path, 0o600)


def load_or_create_keys() -> dict[str, bytes]:
    path = keyring_path()
    if not path.exists():
        keys = {"v1": os.urandom(32), "v2": os.urandom(32)}
        write_keyring("v2", keys)
        return keys
    payload = json.loads(path.read_text())
    encoded = payload.get("keys") if isinstance(payload, dict) else None
    if not isinstance(encoded, dict) or set(encoded) != {"v1", "v2"}:
        raise RuntimeError("The retained recovery keyring is invalid.")
    keys = {key_id: base64.b64decode(value, validate=True) for key_id, value in encoded.items()}
    if any(len(key) != 32 for key in keys.values()):
        raise RuntimeError("The retained recovery keyring is invalid.")
    return keys


def migration_identity() -> list[str]:
    from django.db import connection

    with connection.cursor() as cursor:
        cursor.execute("SELECT app, name FROM django_migrations ORDER BY app, name")
        return [f"{app}:{name}" for app, name in cursor.fetchall()]


def assert_exact_migrations(source: list[str], target: list[str]) -> None:
    if source != target:
        raise RuntimeError("The restored migration identity does not match the source.")


def assert_exact_relationships(expected: dict[str, object], actual: dict[str, object]) -> None:
    for name, expected_value in expected.items():
        if actual.get(name) != expected_value:
            raise RuntimeError("The restored agent foreign keys or statuses are inconsistent.")


def prepare_source() -> None:
    django.setup()
    from django.conf import settings
    from django.core.management import call_command
    from django.db import transaction
    from django.utils.timezone import now

    from zerver.lib.agent_secrets import decrypt_agent_secret, encrypt_agent_secret
    from zerver.models import Message, Realm, UserProfile, agents

    state = state_directory()
    call_command("migrate", interactive=False, verbosity=0)
    if not Realm.objects.filter(string_id="zulip").exists():
        call_command(
            "populate_db",
            test_suite=True,
            num_messages=1,
            threads=1,
            max_topics=1,
            num_direct_message_groups=0,
            num_personals=0,
            percent_direct_message_groups=0,
            percent_personals=0,
            verbosity=0,
        )

    realm = Realm.objects.get(string_id="zulip")
    owner = UserProfile.objects.get(realm=realm, delivery_email="hamlet@zulip.com")
    bot_user = UserProfile.objects.filter(realm=realm, is_bot=True).order_by("id").first()
    source_message = Message.objects.filter(realm=realm).order_by("id").first()
    if bot_user is None or source_message is None:
        raise RuntimeError("The isolated fixture did not create the required user and message.")

    keys = load_or_create_keys()
    keyring_v1 = keyring_path().with_name("keyring-v1.json")
    if not keyring_v1.exists():
        payload = {
            "current": "v1",
            "keys": {
                key_id: base64.b64encode(value).decode("ascii") for key_id, value in keys.items()
            },
        }
        private_file(keyring_v1, (json.dumps(payload, sort_keys=True) + "\n").encode())
    settings.AGENT_ARTIFACT_ROOT = str(artifact_root())
    settings.AGENT_SECRET_MASTER_KEY_FILE = str(keyring_v1)
    ciphertext_v1, wrapped_key_v1, key_id_v1 = encrypt_agent_secret(
        V1_CANARY, realm_id=realm.id, owner_id=owner.id, version=1
    )
    settings.AGENT_SECRET_MASTER_KEY_FILE = str(keyring_path())
    ciphertext_v2, wrapped_key_v2, key_id_v2 = encrypt_agent_secret(
        V2_CANARY, realm_id=realm.id, owner_id=owner.id, version=2
    )

    artifact_bytes = b"synthetic agent recovery artifact\n"
    storage_ref = "completed/recovery/result.patch"
    artifact_path = artifact_root() / storage_ref
    private_directory(artifact_path.parent)
    private_file(artifact_path, artifact_bytes)
    checksum = hashlib.sha256(artifact_bytes).hexdigest()

    with transaction.atomic():
        secret_v1 = agents.AgentSecret.objects.create(
            realm=realm,
            owner=owner,
            ciphertext=ciphertext_v1,
            wrapped_key=wrapped_key_v1,
            key_id=key_id_v1,
            version=1,
        )
        secret_v2 = agents.AgentSecret.objects.create(
            realm=realm,
            owner=owner,
            ciphertext=ciphertext_v2,
            wrapped_key=wrapped_key_v2,
            key_id=key_id_v2,
            version=2,
        )
        runner = agents.AgentRunner.objects.create(
            realm=realm,
            owner=owner,
            name="Recovery rehearsal runner",
            fingerprint="r" * 64,
            status="online",
        )
        provider = agents.AgentProvider.objects.create(
            realm=realm,
            owner=owner,
            runner=runner,
            name="Recovery rehearsal provider",
            base_url="https://provider.invalid/v1",
            model_id="synthetic-model",
            allowed_models=["synthetic-model"],
            secret=secret_v2,
            context_window_tokens=4096,
            max_output_tokens=512,
            data_scope=["synthetic"],
        )
        repository = agents.AgentRepository.objects.create(
            realm=realm,
            owner=owner,
            runner=runner,
            workspace_alias="recovery",
            canonical_origin="https://example.invalid/recovery.git",
        )
        profile = agents.AgentProfile.objects.create(
            realm=realm,
            owner=owner,
            bot_user=bot_user,
            runner=runner,
            provider=provider,
            name="Recovery rehearsal profile",
            adapter_id="codex-acp",
            adapter_version="1.12.0",
        )
        conversation = agents.AgentConversation.objects.create(
            realm=realm,
            profile=profile,
            repository=repository,
            anchor_message=source_message,
        )
        job = agents.AgentJob.objects.create(
            realm=realm,
            requester=owner,
            conversation=conversation,
            profile=profile,
            runner=runner,
            repository=repository,
            source_message=source_message,
            result_message=source_message,
            request="Synthetic recovery request",
            idempotency_key=uuid4(),
            payload_digest="a" * 64,
            status="completed",
        )
        attempt = agents.AgentAttempt.objects.create(
            realm=realm,
            job=job,
            runner=runner,
            number=1,
            lease_epoch=1,
            lease_expires_at=now() + timedelta(minutes=5),
            descriptor_digest="b" * 64,
            process_state="stopped",
            active=False,
            ended_at=now(),
        )
        operation = agents.AgentOperation.objects.create(
            realm=realm,
            attempt=attempt,
            operation_id=uuid4(),
            tool_class="repository.edit",
            argument_digest="c" * 64,
            status="succeeded",
        )
        approval = agents.AgentApproval.objects.create(
            realm=realm,
            job=job,
            attempt=attempt,
            operation=operation,
            approver=owner,
            operation_hash="d" * 64,
            policy_version=1,
            tree_hash="e" * 64,
            decision="consumed",
            expires_at=now() + timedelta(minutes=5),
            decided_at=now(),
            consumed_at=now(),
        )
        artifact = agents.AgentArtifact.objects.create(
            realm=realm,
            attempt=attempt,
            kind="patch",
            checksum=checksum,
            size=len(artifact_bytes),
            storage_ref=storage_ref,
            filename="result.patch",
            expires_at=now() + timedelta(days=1),
        )

    if (
        decrypt_agent_secret(
            secret_v1.ciphertext,
            secret_v1.wrapped_key,
            key_id=secret_v1.key_id,
            realm_id=realm.id,
            owner_id=owner.id,
            version=secret_v1.version,
        )
        != V1_CANARY
    ):
        raise RuntimeError("The retained v1 canary did not decrypt before backup.")
    if (
        decrypt_agent_secret(
            secret_v2.ciphertext,
            secret_v2.wrapped_key,
            key_id=secret_v2.key_id,
            realm_id=realm.id,
            owner_id=owner.id,
            version=secret_v2.version,
        )
        != V2_CANARY
    ):
        raise RuntimeError("The current v2 canary did not decrypt before backup.")

    evidence = {
        "source_revision": os.environ["GROW_TEAM_RECOVERY_SOURCE_REVISION"],
        "reviewed_backup_commit": os.environ["GROW_TEAM_RECOVERY_REVIEWED_BACKUP_COMMIT"],
        "relevant_file_hashes": os.environ["GROW_TEAM_RECOVERY_RELEVANT_FILE_HASHES"].splitlines(),
        "source_note": "The recovery source contains active worktree files; these hashes identify the exercised backup and model sources.",
        "source_migrations": migration_identity(),
        "ids": {
            "realm": realm.id,
            "runner": str(runner.id),
            "provider": str(provider.id),
            "repository": str(repository.id),
            "profile": str(profile.id),
            "conversation": str(conversation.id),
            "job": str(job.id),
            "attempt": str(attempt.id),
            "operation": str(operation.id),
            "approval": str(approval.id),
            "artifact": str(artifact.id),
            "secret_v1": str(secret_v1.id),
            "secret_v2": str(secret_v2.id),
            "source_message": source_message.id,
        },
        "artifact": {"storage_ref": storage_ref, "size": len(artifact_bytes), "checksum": checksum},
        "secret_key_ids": ["v1", "v2"],
        "statuses": {
            "job": job.status,
            "attempt": attempt.process_state,
            "operation": operation.status,
            "approval": approval.decision,
        },
    }
    private_file(
        state / "expected.json", (json.dumps(evidence, sort_keys=True, indent=2) + "\n").encode()
    )


def run_backup() -> None:
    django.setup()
    from django.conf import settings
    from django.core.management import call_command

    state = state_directory()
    settings.AGENT_ARTIFACT_ROOT = str(artifact_root())
    settings.AGENT_SECRET_MASTER_KEY_FILE = str(keyring_path())
    call_command("backup", output=str(state / "backup.tar.gz"), skip_uploads=True)


def verify_target() -> None:
    django.setup()
    from django.conf import settings

    from zerver.lib.agent_backup import verify_agent_backup, verify_agent_backup_references
    from zerver.lib.agent_secrets import decrypt_agent_secret
    from zerver.models import agents

    state = state_directory()
    expected = json.loads((state / "expected.json").read_text())
    target_private = state / "target-private"
    settings.AGENT_ARTIFACT_ROOT = str(target_private / "artifacts")
    settings.AGENT_SECRET_MASTER_KEY_FILE = str(target_private / "keyring.json")

    restored_agent = state / "extracted" / "zulip-backup" / "agent"
    manifest = verify_agent_backup(restored_agent)
    artifact_rows = list(
        agents.AgentArtifact.objects.filter(unavailable_at__isnull=True).values_list(
            "storage_ref", "size", "checksum"
        )
    )
    secret_key_ids = set(agents.AgentSecret.objects.values_list("key_id", flat=True))
    verify_agent_backup_references(
        restored_agent, artifacts=artifact_rows, secret_key_ids=secret_key_ids
    )

    job = agents.AgentJob.objects.select_related(
        "conversation", "profile", "runner", "repository", "result_message"
    ).get(id=expected["ids"]["job"])
    attempt = agents.AgentAttempt.objects.get(id=expected["ids"]["attempt"])
    operation = agents.AgentOperation.objects.get(id=expected["ids"]["operation"])
    approval = agents.AgentApproval.objects.get(id=expected["ids"]["approval"])
    artifact = agents.AgentArtifact.objects.get(id=expected["ids"]["artifact"])
    secret_v1 = agents.AgentSecret.objects.get(id=expected["ids"]["secret_v1"])
    secret_v2 = agents.AgentSecret.objects.get(id=expected["ids"]["secret_v2"])

    ids = expected["ids"]
    assert isinstance(ids, dict)
    assert_exact_relationships(
        {
            "realm": ids["realm"],
            "runner": ids["runner"],
            "provider": ids["provider"],
            "repository": ids["repository"],
            "profile": ids["profile"],
            "conversation": ids["conversation"],
            "job": ids["job"],
            "attempt": ids["attempt"],
            "operation": ids["operation"],
            "approval": ids["approval"],
            "artifact": ids["artifact"],
            "secret_v1": ids["secret_v1"],
            "secret_v2": ids["secret_v2"],
            "source_message": ids["source_message"],
            "job_status": expected["statuses"]["job"],
            "attempt_status": expected["statuses"]["attempt"],
            "operation_status": expected["statuses"]["operation"],
            "approval_status": expected["statuses"]["approval"],
        },
        {
            "realm": job.realm_id,
            "runner": str(job.runner_id),
            "provider": str(job.profile.provider_id),
            "repository": str(job.repository_id),
            "profile": str(job.profile_id),
            "conversation": str(job.conversation_id),
            "job": str(job.id),
            "attempt": str(attempt.id),
            "operation": str(operation.id),
            "approval": str(approval.id),
            "artifact": str(artifact.id),
            "secret_v1": str(secret_v1.id),
            "secret_v2": str(secret_v2.id),
            "source_message": job.result_message_id,
            "job_status": job.status,
            "attempt_status": attempt.process_state,
            "operation_status": operation.status,
            "approval_status": approval.decision,
        },
    )
    if not (
        job.conversation.realm_id == job.realm_id
        and job.conversation.profile_id == job.profile_id
        and job.conversation.repository_id == job.repository_id
        and job.profile.realm_id == job.realm_id
        and job.profile.runner_id == job.runner_id
        and str(job.profile.provider_id) == expected["ids"]["provider"]
        and job.runner.realm_id == job.realm_id
        and job.profile.provider.realm_id == job.realm_id
        and job.profile.provider.runner_id == job.runner_id
        and job.profile.provider.secret_id == secret_v2.id
        and job.repository.realm_id == job.realm_id
        and job.repository.runner_id == job.runner_id
        and attempt.realm_id == job.realm_id
        and attempt.job_id == job.id
        and attempt.runner_id == job.runner_id
        and operation.realm_id == job.realm_id
        and operation.attempt_id == attempt.id
        and approval.realm_id == job.realm_id
        and approval.job_id == job.id
        and approval.attempt_id == attempt.id
        and approval.operation_id == operation.id
        and artifact.realm_id == job.realm_id
        and artifact.attempt_id == attempt.id
        and secret_v1.realm_id == job.realm_id
        and secret_v2.realm_id == job.realm_id
    ):
        raise RuntimeError("The restored agent realm relationships are inconsistent.")

    artifact_path = target_private / "artifacts" / artifact.storage_ref
    content = artifact_path.read_bytes()
    expected_artifact = expected["artifact"]
    if not isinstance(expected_artifact, dict) or not (
        artifact.storage_ref == expected_artifact["storage_ref"]
        and artifact.size == expected_artifact["size"]
        and artifact.checksum == expected_artifact["checksum"]
        and len(content) == artifact.size
        and hashlib.sha256(content).hexdigest() == artifact.checksum
    ):
        raise RuntimeError("The restored agent artifact does not match its database checksum.")
    if (
        decrypt_agent_secret(
            secret_v1.ciphertext,
            secret_v1.wrapped_key,
            key_id=secret_v1.key_id,
            realm_id=secret_v1.realm_id,
            owner_id=secret_v1.owner_id,
            version=secret_v1.version,
        )
        != V1_CANARY
    ):
        raise RuntimeError("The restored retained v1 key cannot decrypt its canary.")
    if (
        decrypt_agent_secret(
            secret_v2.ciphertext,
            secret_v2.wrapped_key,
            key_id=secret_v2.key_id,
            realm_id=secret_v2.realm_id,
            owner_id=secret_v2.owner_id,
            version=secret_v2.version,
        )
        != V2_CANARY
    ):
        raise RuntimeError("The restored v2 key cannot decrypt its canary.")

    evidence = json.loads((state / "expected.json").read_text())
    target_migrations = migration_identity()
    source_migrations = evidence["source_migrations"]
    if not isinstance(source_migrations, list):
        raise RuntimeError("The source migration identity is invalid.")
    assert_exact_migrations(source_migrations, target_migrations)
    evidence["target_migrations"] = target_migrations
    evidence["archive"] = {
        "sha256": hashlib.sha256((state / "backup.tar.gz").read_bytes()).hexdigest(),
        "agent_files": len(manifest["files"]),
    }
    evidence["verified"] = {
        "foreign_keys_and_statuses": True,
        "artifact_checksum": True,
        "retained_v1_decryption": True,
        "current_v2_decryption": True,
    }
    (state / "evidence.json").write_text(json.dumps(evidence, sort_keys=True, indent=2) + "\n")
    os.chmod(state / "evidence.json", 0o600)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "phase", choices=["prepare-source", "backup", "verify-target", "guard-probes"]
    )
    phase = parser.parse_args().phase
    if phase == "prepare-source":
        prepare_source()
    elif phase == "backup":
        run_backup()
    elif phase == "verify-target":
        verify_target()
    else:
        try:
            assert_exact_migrations(["a:one"], ["a:two"])
        except RuntimeError:
            pass
        else:
            raise RuntimeError("The migration mismatch probe did not fail.")
        try:
            assert_exact_relationships({"job": "expected"}, {"job": "different"})
        except RuntimeError:
            pass
        else:
            raise RuntimeError("The relationship mismatch probe did not fail.")


if __name__ == "__main__":
    main()
