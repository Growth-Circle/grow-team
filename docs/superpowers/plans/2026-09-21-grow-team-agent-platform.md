# Grow Team Agent Platform Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Track each step with checkboxes.

**Goal:** Complete both approved agent specifications, verify the supported Linux modes, and move completed specifications to `internals/docs/done/`.

**Architecture:** Django owns identity, permissions, configuration, jobs, leases, approvals, and publication. A separate TypeScript runner owns local execution. Rootless containers isolate project code and native agents. PostgreSQL stores the durable journal.

**Tech Stack:** Existing Django, PostgreSQL, TypeScript, jQuery, Handlebars, and RabbitMQ. The runner uses pinned Node and ACP packages.

**Spec:**
- `internals/docs/spec/2026-09-21-agent-connections-and-coding-harness.md`
- `internals/docs/spec/2026-09-21-agent-lifecycle-and-mention-flow.md`

## Global Constraints

- Keep Grow Team on Zulip. Do not migrate the chat platform.
- Support Linux first. Certify one installed ACP agent and one real model endpoint.
- Support Chat Completions and Responses with separate wire schemas.
- Use one selected endpoint runtime. Record the Buzz comparison before selecting it.
- Keep all member interactions in the browser. A closed tab must not stop a job.
- Do not modify Hermes services, settings, data, or containers.
- Do not modify the user's active repository checkout during an agent job.
- Use additive migrations and a disabled feature flag for each realm.
- A platform administrator does not inherit permission to use another person's runner.
- Store device credential hashes. Encrypt server provider secrets with an external key.
- Keep provider secrets, Git credentials, and control credentials outside project processes.
- Bind each mutable request to the current realm, actor, job, attempt, version, and lease epoch as applicable.
- Keep approval, verification, and completion authority outside model output.
- Preserve upstream notices when code is reused.
- End each commit with `Co-Authored-By: CADIS <agent@cadis.digital>` after a blank line.
- Keep incomplete specifications in `internals/docs/spec/`.

Pilot limits from the specifications:

| Limit | Value |
| --- | --- |
| Pairing lifetime | 10 minutes |
| Access credential lifetime | 24 hours |
| Refresh credential lifetime | 30 days; rotate on use |
| HTTP wait | At most 25 seconds |
| Heartbeat / lease | 15 / 90 seconds |
| Active jobs | 1 per runner; 2 per realm |
| Queued jobs | 20 per profile; 100 per realm |
| Queue start deadline | 24 hours |
| Active job duration | 60 minutes; configured maximum 120 minutes |
| Model tool rounds | 40 |
| Shell timeout | 2 minutes; approved checks at most 20 minutes |
| Transport retries | 2 per operation |
| Context recovery | 2 attempts per turn |
| Approval lifetime | 15 minutes |
| Event / model tool output | 64 KiB / 50 KiB |
| Artifact size | 10 MiB per file; 50 MiB per job |
| Send retry / mapping retention | 24 hours / at least 30 days and while a job remains active |
| Retention defaults | Events 30 days; artifacts 7 days; approval metadata 90 days |

Retention expiry must not delete active results or the user's work. Automatic destructive cleanup remains disabled until the owner enables its policy.

## Contract and file boundaries

Use the existing `/json/agent/...` and `/api/v1/agent/...` routes for human requests. Use dedicated bearer authentication for device routes.

All successful JSON responses include `result: "success"`, `msg: ""`, and `schema_version: 1`. Errors use stable codes and safe messages.

Human mutations carry a form field `payload` containing JSON. Device requests carry a JSON body. Path IDs remain UUIDs except existing Zulip IDs.

The shared descriptors and response fixtures live in `zerver/tests/fixtures/agents/protocol-v1.json`. Python and runner tests validate the same fixtures.

```typescript
type LeaseIdentity = {
    job_id: string;
    attempt_id: string;
    lease_epoch: number;
};
type RunnerEvent = LeaseIdentity & {
    schema_version: 1;
    event_id: string;
    sequence: number;
    type: string;
    occurred_at: string;
    payload: Record<string, unknown>;
};
type AttemptDescriptor = LeaseIdentity & {
    schema_version: 1;
    profile_id: string;
    profile_revision: number;
    descriptor_digest: string;
    lease_expires_at: string;
    job_kind: "answer" | "code";
    delivery_target: "answer" | "patch" | "draft_pr";
    request: string;
    runner_id: string;
    adapter: {id: string; version: string; mode: "acp" | "endpoint"};
    provider: Record<string, unknown> | null;
    repository: Record<string, unknown> | null;
    policy: Record<string, unknown>;
    budget: Record<string, number>;
    context_refs: Record<string, unknown>[];
    inputs: Record<string, unknown>[];
    checkpoint: Record<string, unknown> | null;
};
```

Resolve the nested records in Task 1 before parallel consumers start. Never include plaintext secrets in a descriptor.

Backend modules have these responsibilities:

| Files | Responsibility |
| --- | --- |
| `zerver/models/agents.py`, migrations | Durable records, indexes, uniqueness |
| `zerver/lib/agent_protocol.py` | Validated payloads, descriptors, limits, safe errors |
| `zerver/lib/agent_policy.py` | Current actor, realm, runner, profile, repository, provider, and conversation permissions |
| `zerver/lib/agent_secrets.py` | Envelope encryption and credential grants |
| `zerver/actions/agent_connections.py` | Pairing, rotation, runner and repository registration, providers |
| `zerver/actions/agent_profiles.py` | Profile revision, bot identity, setup, readiness, lifecycle, channel attachment |
| `zerver/actions/agent_jobs.py` | Job/input state, claim, lease, controls, events, stop confirmation |
| `zerver/actions/agent_dispatch.py` | Admission, receipts, send intent, manual and mention triggers |
| `zerver/actions/agent_approvals.py` | Proposal, decision, consumption, operation reconciliation |
| `zerver/lib/agent_context.py` | Current message access, references, and audience checks |
| `zerver/lib/agent_results.py` | Private artifacts, verification, result gates, atomic publication |
| `zerver/lib/agent_reconcile.py`, management command | Outbox replay, expired leases, deadlines, telemetry |
| `zerver/views/agents.py`, `agent_runner.py` | Human and device API boundaries |

## Task 0: Runtime decision and isolated test environment

**Files:** `internals/docs/agent-runtime-decision.md`; `tools/grow-team/test-environment/`; runner conformance fixtures.

**Interfaces:** Produce pinned runtime versions, adapter capability matrix, and reproducible backend/runner test commands.

- [ ] Compare pinned Buzz subprocess behavior with a bounded TypeScript runtime.
- [ ] Verify real ACP initialization without loading personal credentials or calling a model.
- [ ] Record permission, environment, persistence, streaming, and sandbox limitations.
- [ ] Create dedicated local test services and an independent Python environment.
- [ ] Run an existing Django test before adding application behavior.
- [ ] Preserve a real browser-to-runner ACP proof as a release gate in Task 11.

Probe assertion:

```typescript
assert.equal(handshake.protocolVersion, 1);
assert.equal(promptCalls, 0);
assert.equal(credentialFilesRead, 0);
```

## Task 1: Durable models and protocol

**Files:** `zerver/models/agents.py`, `zerver/models/__init__.py`, the next additive migration, `zerver/lib/agent_protocol.py`, protocol fixtures, `zerver/tests/test_agents_models.py`.

**Interfaces:** Produce all model names from both specifications, plus `AgentRealmSettings`, `AgentPairing`, and `AgentVerification`. Produce validated version 1 payloads and serializers.

- [ ] Add failing tests for duplicate active attempts, trigger keys, send keys, event IDs, operations, and delivery keys.
- [ ] Add realm, owner, revision, state, timestamp, and foreign-reference fields required by both specifications.
- [ ] Add database constraints and queue/audit indexes.
- [ ] Define exact profile, provider, repository, grant, descriptor, event, approval, and artifact schemas.
- [ ] Reject unknown event authority, excessive payload size, and invalid state values.
- [ ] Generate the migration and prove its forward application in the isolated database.
- [ ] Run model/protocol tests, inspect migration state, and commit the task.

Test example:

```python
with self.assertRaises(IntegrityError), transaction.atomic():
    AgentAttempt.objects.create(job=job, number=2, active=True, **attempt_fields)
self.assertEqual(AgentAttempt.objects.filter(job=job, active=True).count(), 1)
```

## Task 2: Connections, credentials, policy, and profile readiness

**Files:** `agent_policy.py`, `agent_secrets.py`, connection/profile actions, human/device views, URLs, `test_agents_connections.py`, `test_agents_policy.py`.

**Interfaces:** Produce `check_agent_access(actor, profile, repository, source_message, action)`, profile DTOs, probe descriptors, and device principals. No model call occurs in Django.

- [ ] Test anonymous pairing expiry, replay, brute force, approval realm derivation, rotation, and revocation.
- [ ] Add immutable device ownership and hashed credentials with constant-time checks.
- [ ] Implement encrypted write-only provider credentials and owner-local secret references.
- [ ] Implement owner-registered repository aliases without accepting browser host paths or shell commands.
- [ ] Implement explicit member/group grants and intersection checks across all resources.
- [ ] Create bot, profile, and setup records atomically with idempotency keys.
- [ ] Reject stale probe revisions; enable only the descriptor that passed readiness.
- [ ] Implement pause, archive, and channel membership/grant retry without duplicate identities.
- [ ] Test denied cross-realm list/count/detail and admin-without-device-grant cases.
- [ ] Run focused tests and commit the task.

Test example:

```python
first = create_profile(owner, payload, idempotency_key="profile-one")
second = create_profile(owner, payload, idempotency_key="profile-one")
self.assertEqual(first.id, second.id)
self.assertEqual(AgentProfile.objects.filter(bot_user=first.bot_user).count(), 1)
```

## Task 3: Durable job lifecycle, leases, controls, and result gates

**Files:** job/approval actions, context/results/reconcile libraries, management command, runner views, lifecycle/result tests.

**Interfaces:** Produce device claim/lease/control/event/context/artifact/credential APIs. Claim returns the frozen Task 1 descriptor. Human APIs return jobs, inputs, events, artifacts, and allowed actions.

- [ ] Test concurrent claims and lost claim responses with actual database rows.
- [ ] Enforce capacity, start deadlines, schema versions, record versions, epochs, and current grants.
- [ ] Persist event receipts and ordered inputs before acknowledgement.
- [ ] Implement cancel requests, stop confirmation, interrupted state, and explicit new-attempt resume.
- [ ] Implement bounded outbox replay and lease/deadline reconciliation without network work inside message transactions.
- [ ] Implement proposals and single-use approval consumption bound to operation, diff, policy, and attempt.
- [ ] Keep uncertain remote operations blocked until a matching receipt is reconciled.
- [ ] Store private bounded artifacts and structured final-tree verification records.
- [ ] Reject model-only completion, stale checks, missing required checks, and unconfirmed delivery.
- [ ] Publish one result atomically after current audience checks.
- [ ] Test stop/completion races, authority violations, secret redaction, and publication retry.
- [ ] Run focused tests and commit the task.

Test example:

```python
first = claim_work(runner, claim_key="claim-one")
second = claim_work(runner, claim_key="claim-one")
self.assertEqual(first["attempt_id"], second["attempt_id"])
self.assertEqual(AgentAttempt.objects.filter(job=job, active=True).count(), 1)
```

## Task 4: Authoritative mention admission and send idempotency

**Files:** `agent_dispatch.py`, message send/render paths, message API, admission tests.

**Interfaces:** Produce transaction-bound receipts and jobs from actual personal mention provenance. Expose preflight, dispatch receipt, and send-intent reconciliation APIs.

- [ ] Add renderer fixtures for personal/group/wildcard/silent/code/blockquote mentions.
- [ ] Capture personal mention metadata before group expansion. Preserve existing service bots.
- [ ] Insert admission after message IDs exist and inside the message transaction.
- [ ] Reject bot authors and edits as automatic triggers.
- [ ] Apply the exact one-to-one DM and group DM rules.
- [ ] Add sender-scoped `agent_send_key` and digest conflict handling without changing legacy client behavior.
- [ ] Deduplicate targets, preserve rejected receipts, and return draft jobs when coding fields are incomplete.
- [ ] Recheck permission after preflight and verify zero spawn on denial.
- [ ] Route follow-up only by explicit job identity.
- [ ] Run message and admission regressions and commit the task.

Test example:

```python
self.send_stream_message(sender, "private", "> @**Agent|42**")
self.assertEqual(AgentJob.objects.count(), 0)
self.assertEqual(AgentOutbox.objects.count(), 0)
```

## Task 5: Runner package, registration, transport, and durable journal

**Files:** `services/grow-agent-runner/`, workspace manifest, runner CLI/transport/config/journal tests.

**Interfaces:** Consume Task 1 protocol and Task 2–3 APIs. Produce local registered workspace/adapter catalogs and a supervisor dispatch interface.

- [ ] Pin Node, ACP SDK, adapter, and runtime dependencies from Task 0.
- [ ] Implement pairing, local secret references, token rotation, workspace registration, and owner-controlled adapter catalogs.
- [ ] Protect configuration and journal files with owner-only permissions.
- [ ] Persist outgoing events, claims, inputs, and operation receipts before acknowledgement.
- [ ] Poll with bounded requests and backoff. Reconcile leases after reconnect.
- [ ] Freeze control-plane identity and reserved environment keys.
- [ ] Test restart with lost claim/event acknowledgements and expired/revoked credentials.
- [ ] Supply a user service and doctor command without exposing inbound ports.
- [ ] Run runner transport tests and commit the task.

Test example:

```typescript
await journal.append(event);
await transport.flush();
await transport.flush();
assert.equal(server.persistedEvents(event.event_id), 1);
```

## Task 6: Workspace, rootless sandbox, tools, and verifier

**Files:** runner workspace/sandbox/tool/verification modules and real process fixtures.

**Interfaces:** Produce `prepareWorkspace`, `runSandboxedTool`, `stopAttempt`, and `verifyFinalTree`. All accept the frozen attempt descriptor and a current lease guard.

- [ ] Create an independent checkout from the approved base commit, without sharing writable common Git metadata.
- [ ] Preserve dirty user WIP and validate origin, refs, hooks, filters, submodules, symlinks, and sensitive paths.
- [ ] Limit mounts, UID, capabilities, CPU, memory, process count, time, and egress in rootless containers.
- [ ] Keep control/Git/provider secrets outside native agents and repository processes.
- [ ] Implement bounded read/search/edit/shell tools with argument validation and durable tool IDs.
- [ ] Stop the entire process tree on cancel, timeout, or lease loss.
- [ ] Run owner-defined required checks and bind results to the final tree hash.
- [ ] Produce checksummed diff/output artifacts and retain recoverable workspaces.
- [ ] Prove cross-path/secret/network denial and grandchild termination with real processes.
- [ ] Run sandbox/verifier tests and commit the task.

Test example:

```typescript
await sandbox.cancel(attempt);
assert.equal(await processExists(childPid), false);
assert.equal(await processExists(grandchildPid), false);
assert.equal(await hashUserCheckout(), originalUserCheckoutHash);
```

## Task 7: ACP and the selected endpoint runtime

**Files:** runner ACP/runtime/provider/broker modules and conformance fixtures.

**Interfaces:** Both modes implement `probe`, `startSession`, `sendTurn`, `cancel`, `close`, and capability-gated resume. Both use the same supervisor, policy, verifier, and publisher.

- [ ] Implement negotiated ACP session setup, updates, option-ID permission decisions, cancel, and no-load fallback.
- [ ] Enforce policy with the outer sandbox. Do not trust native adapter mode names or automatic-review defaults.
- [ ] Implement the selected endpoint runtime with distinct Chat Completions and Responses codecs.
- [ ] Validate DNS/IP and each connection, allow only owner-approved private targets, reject metadata and credential-crossing redirects.
- [ ] Probe synthetic tool round-trips and distinguish passed, unsupported, and unknown capabilities.
- [ ] Assemble complete tool arguments before schema validation and execution.
- [ ] Preserve tool/result IDs and uncertain outcomes across cancellation and recovery.
- [ ] Bound retries, rounds, time, output, and context recovery. Preserve current input exactly once.
- [ ] Make answer mode incapable of mutation.
- [ ] Test both modes with fake ACP/provider processes and real sandbox tools.
- [ ] Run conformance tests and commit the task.

Test example:

```typescript
await runtime.acceptChunk({arguments: '{"path":"src/x.ts",'});
await runtime.cancel();
assert.equal(toolBroker.mutationCount, 0);
```

## Task 8: Remote operation broker and bounded Titen integration

**Files:** runner Git operation, approval, reconciliation, and memory modules; corresponding backend operations tests.

**Interfaces:** Consume server-approved operation hashes and return verifiable remote receipts. Memory uses authorized stable subjects and resolved repository projects.

- [ ] Keep Git write credentials outside the project sandbox.
- [ ] Bind push to the reviewed commit, remote, task branch, and expected remote head.
- [ ] Create draft PRs only from approved title/body/base/head and reconcile uncertain responses.
- [ ] Reject changed diff, expired approval, stale policy, and double consumption.
- [ ] Resolve lowercase Git origin before one bounded Titen compile per task.
- [ ] Treat memory as untrusted reference data. Degrade safely on outage.
- [ ] Permit durable memory writes only for explicitly authorized, typed, verified signals.
- [ ] Test local bare-remote races and a real HTTP Git-provider fixture with lost acknowledgements.
- [ ] Run focused tests and commit the task.

Test example:

```typescript
await broker.push(approvedOperation);
await broker.reconcile(approvedOperation);
assert.equal(await remoteHead(taskBranch), approvedCommit);
assert.equal(provider.createdPullRequests(operationId), 1);
```

## Task 9: Browser settings and job panel

**Files:** settings/agent jobs TypeScript, Handlebars, styles, settings registration, frontend tests.

**Interfaces:** Consume only human APIs and versioned DTOs. Browser code never receives device credentials or plaintext stored secrets.

- [ ] Add runner pairing/approval, status, revocation, repository registration guidance, and explicit grants.
- [ ] Add provider forms, write-only secret input, local references, versioned probes, and capability limits.
- [ ] Add profile create/edit/check/enable/pause/archive and channel attachment flows with recoverable drafts.
- [ ] Separate desired profile state, runner liveness, readiness, capability, process state, and job state.
- [ ] Add job list/detail, input status, diff, required check evidence, approval, cancel, and resume controls.
- [ ] Render unknown status, stale readiness, denied access, blocked publication, and partial setup honestly.
- [ ] Escape output and progressively render bounded diffs.
- [ ] Test keyboard focus, labels, dark/light themes, narrow screens, and API failure recovery.
- [ ] Run frontend tests, lint, typecheck, and commit the task.

Test example:

```typescript
await renderStatus({runner_status: "unknown", cached_status: "online"});
assert.equal(screen.getByText("Status runner belum dapat diperiksa.").isVisible(), true);
assert.equal(spawnRequests.length, 0);
```

## Task 10: Composer, message actions, and follow-up integration

**Files:** compose state/send/typeahead paths, message actions, job panel hooks, deferred frontend tests.

**Interfaces:** Send the exact captured profile IDs, destination, draft revision, visit token, send key, and explicit target job. Reconcile receipts through Task 4 APIs.

- [ ] Add agent selection and message-to-task actions using stable identities.
- [ ] Capture an immutable send snapshot before preflight or upload.
- [ ] Cancel stale continuations before publish and preserve edited or deliberately cleared drafts.
- [ ] Keep chat send independent of runner start; distinguish sent message from accepted task.
- [ ] Recover lost responses with the same send key and dispatch receipts.
- [ ] Add explicit follow-up controls and pending/applied/uncertain input states.
- [ ] Test A-to-B-to-A navigation, rename, repeated targets, multi-target receipts, and two concurrent jobs.
- [ ] Run compose/message regressions and commit the task.

Test example:

```typescript
const intent = captureSendIntent();
editDraft("");
await resolvePreflight(intent);
assert.equal(publishedMessages.length, 0);
assert.equal(currentDraftText(), "");
```

## Task 11: Full acceptance, real runtime certification, and API documentation

**Files:** acceptance matrix, backend/frontend/runner tests, OpenAPI/changelog, runtime decision and certification documents.

**Interfaces:** Produce an evidence row for every AT-01–AT-36 and AF-01–AF-38 requirement on supported modes.

- [ ] Test database crash windows, row-lock races, expiry, ACL revocation, and publication deduplication.
- [ ] Test fake-provider failure modes and real Linux process boundaries.
- [ ] Certify one installed ACP agent and one real endpoint with bounded synthetic fixture tasks.
- [ ] Exercise browser-to-Django-to-background-runner flows in both modes without a desktop app.
- [ ] Use owner, authorized member, unauthorized member, private channel, and isolated fixture repository.
- [ ] Verify tab-close persistence, explicit follow-up, cancel, offline queue recovery, and cross-realm denial.
- [ ] Complete OpenAPI and unmerged API changelog without manually changing feature level.
- [ ] Record supported versions, license sources, limits, and actual command results.
- [ ] Run broad branch review and repair confirmed findings before deployment.

Acceptance mapping:

| Work | Mandatory evidence |
| --- | --- |
| Tasks 1–2 | AT-01–03, AT-22, AT-32; AF-01–06, AF-33–34 |
| Tasks 3–4 | AT-11–17, AT-21–28, AT-31; AF-07–18, AF-21–26, AF-28–36 |
| Tasks 5–7 | AT-04–10, AT-15–20, AT-27–29, AT-32, AT-34; AF-14–18, AF-25–32, AF-37–38 |
| Task 8 | AT-24–26, AT-30, AT-32; AF-30–32 |
| Tasks 9–10 | AT-33, AT-36; AF-01–06, AF-19–26, AF-28–37 |
| Tasks 11–12 | Every preceding row, AT-35, production and recovery evidence |

## Task 12: Release, recovery proof, and specification completion

**Files:** deployment configuration, runner service/runbook, product documents, acceptance evidence, specification paths.

**Interfaces:** A verified production release with a reversible feature flag and preserved chat data.

- [ ] Build the pinned application image and runner package with additive migrations and flags disabled.
- [ ] Back up database, artifacts, encrypted secrets, and configuration. Verify copied checksums.
- [ ] Restore to an isolated target and verify job/approval/result references and artifact checksums.
- [ ] Rehearse compatible runner rollback and feature shutdown without removing journals or chat data.
- [ ] Activate only the selected realm, runner, and fixture after security and cancellation gates pass.
- [ ] Run production smoke, bounded capacity checks, and Hermes isolation checks.
- [ ] Commit and push; verify remote SHA and runtime source identities.
- [ ] Update the eight product documents with implemented behavior and actual limitations.
- [ ] Move each specification to `internals/docs/done/` only when its acceptance matrix is complete.
- [ ] Update links and commit/push the completion evidence.

## Controller rulings

- The user approved implementation of the existing architectural specifications. Do not repeat the design approval request.
- Future platform migration, hosted runners, marketplace, billing, multi-agent execution, Windows, merge, and deployment tools remain outside initial scope.
- Rootless test services must be dedicated to this work. Do not reuse another project's database or change host services.
- The feature flag uses a new realm-scoped settings record to keep the integration isolated from existing chat settings.
- Test fixtures may use synthetic principals and providers. Real ACP and endpoint certification remain separate, mandatory gates.
- Do not claim success for a missing real-runtime or recovery gate. Continue independent work and record the exact remaining condition.
