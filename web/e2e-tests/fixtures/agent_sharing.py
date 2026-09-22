"""Create separate resource owners in the dedicated browser test database."""

import json
import os
import sys
from uuid import uuid4

sys.path.insert(0, os.getcwd())
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "zproject.test_settings")

import django

django.setup()

from zerver.actions import agents as actions
from zerver.models import UserProfile, agents

profile_owner = UserProfile.objects.get(delivery_email=sys.argv[1])
members = list(
    UserProfile.objects.filter(
        realm=profile_owner.realm,
        role=UserProfile.ROLE_MEMBER,
        is_bot=False,
        is_active=True,
    )
    .exclude(id=profile_owner.id)
    .exclude(delivery_email="")
    .order_by("id")[:4]
)
assert len(members) == 4
runner_owner, provider_owner, repository_owner, ordinary_member = members
for user in members:
    user.set_password("task9-synthetic-browser-password")
    user.save(update_fields=["password"])

catalog = {
    "revision": 1,
    "adapters": [
        {"id": "acp", "version": "1", "auth_state": "ready", "capabilities": {"config_version": 1}}
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
runner = agents.AgentRunner.objects.create(
    realm=profile_owner.realm,
    owner=runner_owner,
    name="Shared test device",
    host_kind="server",
    fingerprint=uuid4().hex * 2,
    catalog_report=catalog,
    catalog_revision=1,
)
provider = agents.AgentProvider.objects.create(
    realm=profile_owner.realm,
    owner=provider_owner,
    runner=runner,
    name="Shared test model",
    base_url="https://example.com",
    model_id="synthetic-share-model",
    allowed_models=["synthetic-share-model"],
    context_window_tokens=8192,
    max_output_tokens=1024,
    local_credential_ref="test-local-ref",
    data_scope=["synthetic"],
    capability_report={"config_version": 1},
)
repository = agents.AgentRepository.objects.create(
    realm=profile_owner.realm,
    owner=repository_owner,
    runner=runner,
    workspace_alias="shared-evidence",
    allowed_refs=["main"],
)
for owner, kind, target, action in [
    (runner_owner, "runner", runner, "runner.use"),
    (provider_owner, "provider", provider, "provider.use"),
    (repository_owner, "repository", repository, "repository.read"),
]:
    actions.create_agent_grant(
        owner,
        principal_user=profile_owner,
        target_kind=kind,
        target=target,
        actions=[action],
    )
profile = actions.create_profile(
    profile_owner,
    name="Four owner sharing profile",
    runner=runner,
    adapter_id="acp",
    adapter_version="1",
    mode="endpoint",
    provider=provider,
    repository=repository,
    provider_network_version=provider.config_version,
    idempotency_key=uuid4(),
)
print(
    json.dumps(
        {
            "profile_id": str(profile.id),
            "runner_id": str(runner.id),
            "provider_id": str(provider.id),
            "repository_id": str(repository.id),
            "runner_owner": runner_owner.delivery_email,
            "provider_owner": provider_owner.delivery_email,
            "repository_owner": repository_owner.delivery_email,
            "ordinary_member": ordinary_member.delivery_email,
            "ordinary_member_id": ordinary_member.id,
        }
    )
)
