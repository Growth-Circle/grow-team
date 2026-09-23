# Contained runtime

Task 7 supplies the endpoint runtime, the pinned ACP adapter, and the host model broker.
The CLI uses `RuntimeSupervisor`. It keeps model turns outside the control callback queue.
Task 8 supplies remote Git effects and final publication. Task 11 certifies real providers and coding readiness.

## Install and build

Use Node 24.18.0 on Linux x64. Install the exact production dependencies from `package-lock.json`.

```bash
npm ci --ignore-scripts --no-audit --no-fund
npm run build
npm run patch:native
npm test
npm run assemble
```

Assembly creates a separate distribution below `release/`. It includes production dependencies, native payloads, notices, source records, and an SBOM.
Build the model image from that distribution. Use its immutable image ID in the owner configuration.

```bash
docker --host unix:///run/user/$(id -u)/docker.sock build --network=none --pull=false -f native/Dockerfile -t grow-agent-model:reviewed RELEASE_DIRECTORY
```

The image uses the pinned Node 24.18.0 Trixie base. The tool image stays separate.
The adapter patch accepts only the recorded upstream bundle hash. Each patch target must occur once.
Launch verifies the patched bundle and policy hashes. Keep all upstream licenses and notices.
Rebuild the image when the patch, policy, package lock, or compiled runtime changes.

Create owner-only `runtime.json` in the runner state directory. Set these fields:

- `owner_approved`: explicit local approval, as `true`.
- `docker`: the approved local Docker executable.
- `endpoint`: `unix:///run/user/UID/docker.sock` for the current user.
- `images`: immutable IDs for the approved model and tool images.
- `model_image`: the approved model image ID.

Register the matching owner catalog through the existing CLI.
Use adapter version `0.1.0` for endpoint mode and `1.12.0` for ACP mode.
ACP requires a Responses provider. Endpoint mode supports Chat Completions and Responses.
Missing installation approval, dependency pins, catalog approval, or chat evidence prevents job claims.
Setup probes can run before chat readiness exists. Each probe needs a current server setup grant.
A probe reports five setup conditions as a plain requirement instead of an error: a missing or unsupported adapter, a required or unclear adapter sign-in, and an unapproved sandbox. Each requirement names one action: install the adapter, sign in the adapter, or approve the sandbox.
The runtime does not report real coding certification from synthetic fixtures.

## Authority and containment

The host broker owns provider credentials. The model container gets no provider, runner, or Git credential.
Each model container has network disabled, a read-only root, fixed resource limits, and no project mount.
A single read-only Unix socket mount carries model and tool requests to the host broker.
The private socket parent stays outside the container. The tool container has no socket mount.
Socket ownership records remain in the runner state directory. Private socket directories remain below `/run/user/UID/grow-broker-*`.
The runner validates ownership, private mode, and absence of symlinks. This path remains shared when the service uses `PrivateTmp=yes`.
Each session creates a new container and socket. An old mount cannot acquire a replacement socket inode.

The native shared request gate fixes environments, model, approval policy, and turn sandbox policy.
Thread creation uses read-only policy with no native environments. Turns use `externalSandbox` with restricted network.
The patch disables title generation. It rejects unproved review, fork, resume, goal, and settings paths.
The host checks the exact effective tool catalog on every native model request.
ACP permissions select an offered rejection ID. Native permission options never grant Grow operation authority.

Every provider effect checks current job or setup authority. Local credentials use the same server authority checks.
Provider data scope must permit synthetic probes, selected chat, or selected repository data as applicable.
DNS checks classify public, private, shared Tailnet, loopback, and forbidden addresses.
Private HTTP requires exact hostname and port approval. Metadata denial overrides private target approval.
Each connection uses a validated address. HTTPS verifies the approved hostname and certificate.
The broker fixes the origin, route, model, token limit, and authorization header.
It rejects redirects and remote media. It has no discovery or fallback-provider path.

The endpoint loop assembles the complete response before any tool effect.
A malformed or partial stream cannot dispatch a tool. Tool calls require catalog, argument, and identity validation.
Answer mode exposes only approved read tools. Tools require durable local intent and consumed server operation authority.
The server operation hash remains distinct from the raw argument digest.
Tool and provider reservations survive journal restart. Uncertain effects and uploads require explicit recovery.

Known secrets are removed before shared artifact retention, checksums, answers, and tool output receipts.
Secret-bearing tool arguments are rejected. Replacing their content could change the requested operation.
Reasoning content is discarded. Adapter stderr and provider error bodies are not retained as diagnostics.

## Recovery and supported limits

The Coordinator serializes events, operations, uploads, checkpoints, context, and input callbacks.
It refreshes the monotonic job version without changing immutable attempt identity.
Long model turns do not block control polling. Lease loss stops both contained process classes.
Setup checks observe the existing claim and grant. They cannot extend a grant or claim another lease.
The independent watchdog discovers model and tool containers through the same installation labels.
Stopped containers, volume metadata, snapshots, socket records, and artifacts remain available for inspection.

Native session load and active steering remain unsupported. Both modes queue additional input for the next legal turn boundary.
The runner records pending input before dispatch. It records the runtime outcome before acknowledging the input to the server.
The input cursor advances only after that acknowledgement. Checkpoints use the acknowledged cursor.
An uncertain dispatch cannot replay automatically. A lost acknowledgement can use the durable runtime receipt through input reconciliation.

Before result preparation, the runtime polls and drains accepted input. The model session stays available until the next control or heartbeat poll closes the input boundary.
That poll marks a valid prepared result as stopping only when no accepted input remains unapplied.
Input accepted before this boundary invalidates the result and keeps execution active. Input after the boundary is rejected.
Confirmed stop evidence then permits result publication. Publication still requires empty containment.
A later input wakes the queue. The original active deadline remains in force.
The runner stops on cancellation or deadline. A stopped interrupted attempt requires explicit recovery.
`attemptDeadline(d)` derives the attempt ceiling from the lease expiry and the active-second budget, minus a five-second margin. `execute()` uses the smaller of that ceiling and the local budget deadline for the guard, the model authority, and the abort timer. Every socket request to the tool and model broker waits for that same remaining time, with no separate margin for a tool call. The sandbox shell timeout still bounds each command.

Each published repository checkpoint retains a local immutable snapshot under the owner-only runner state directory.
Recovery accepts only the server-selected checkpoint with matching job, source attempt, repository policy, base commit, and complete checkpoint record.
It checks the retained archive hash and final tree before publishing `workspace.prepared` or starting a model session.
A missing or changed snapshot blocks recovery. It never falls back silently to the base tree.
A fresh session receives bounded summary, remaining work, next step, context references, and current request data.
Pending input runs as a separate identified turn. Checkpoint text has no authority.
Context recovery preserves the active input once. It does not replay uncertain tools.

Cleanup always attempts scope stop, including after runtime close failure.
An unconfirmed stop writes `containment-recovery.json` and blocks probes and jobs.
Inspection clears that block only after it confirms no owned active containers.
Hard monetary caps fail closed until reviewed pricing reservations exist.
Token reservations use conservative input byte counts and fixed output ceilings.
An uncertain provider request retains its reservation. Only explicit 429 or 5xx responses permit bounded retries.

Run isolated integration checks with the reviewed image tags:

```bash
npm run test:runtime
```

This suite uses synthetic providers and real rootless containers. It makes no real model calls.

## Coding certification evidence

The plain-chat probe sends no tool fields. A separate tool session uses the same provider and dialect.
A text-only provider can retain chat readiness after an explicit tool-request rejection. It cannot gain coding readiness.

Synthetic probes alone keep `code_ready=false`. Task 11 must run real-provider certification before the owner installs coding evidence.
The evidence file and its approved SHA-256 remain in the owner-only runner state directory.
No model process can read or write these files. There is no external certification service or signing-key lifecycle.

Task 11 must use the exact current probe configuration and distribution for these tests:

1. Read a selected repository file through a consumed Grow operation.
2. Edit that repository through a consumed Grow operation.
3. Verify that the final tree differs from the approved base tree.
4. Run every owner-required check on that final tree with zero exit status and no timeout.
5. For patch-only policy, prepare the final diff and retain its artifact identity and delivery receipt.
6. For remote publication policy, retain the trusted Git receipt and prove conflict rejection and idempotent replay.
7. Prove model and tool containment after cancellation and checkpoint recovery.
8. Retain provider request identifiers and the bounded test records outside model access.

Record real results. Do not promote the synthetic fixtures used by unit tests.
Hash retained outputs, receipts, provider identifiers, and containment evidence with SHA-256.
Patch-only evidence needs no remote credential, push grant, or remote receipt.
Verify those artifact hashes before owner approval. The installer validates bindings and result fields; it cannot rerun historical provider work.
The owner approval establishes trust in the retained test evidence.

Construct `evidence.json` with these fields:

- `version`: `1`.
- `issued_at` and `expires_at`: UTC timestamps, with at most 30 days between them.
- `configuration_digest`: the selected probe descriptor digest for execution configuration.
- `configuration`: the exact output from `effectiveConfiguration(descriptor)`.
- `package`: the exact output from `runtimePackageIdentity(config.model_image)`.
- `tools`: the exact Grow coding tool catalog for that descriptor and repository binding.
- `cases.provider`: `real_provider: true` and `request_ids_sha256`.
- `cases.read`: `passed: true` and `output_sha256`.
- `cases.edit`: `passed: true`, different `before_tree` and `after_tree`, and `diff_sha256`.
- `cases.checks.definitions`: the exact approved repository check definitions.
- `cases.checks.results`: one record per check with `check_id`, `exit_code: 0`, `timed_out: false`, final `tree_hash`, and `output_sha256`.
- `cases.publication`: `passed: true` and final `tree_hash`.
- For patch-only policy, include `kind: "patch"`, `artifact_id`, `diff_artifact_sha256`, and `delivery_receipt_sha256`.
- For policy with `git.push` or `git.draft_pr`, include `kind: "remote"`, `conflict_rejected: true`, `replay_idempotent: true`, and `remote_receipt_sha256`.
- `cases.containment`: `model_stopped: true`, `tool_stopped: true`, `cancellation_passed: true`, `recovery_passed: true`, and `evidence_sha256`.

The approved repository must define at least one required check.
`runtimePackageIdentity` binds the model image, compiled host modules, protocol schema, package lock, native patch manifest, and Node version.
The execution configuration binds the runner, profile revision, provider, credentials version, adapter, repository policy, tools, sandbox, network policy, and budgets.
The check definitions must match the repository binding digest. All checks and publication must identify the tested edited tree.
Changed bytes, configuration, scope, version, results, or expiry reject the evidence.

Keep both input files owner-only. From the tested distribution, run this explicit owner installation command:

```bash
node scripts/install-coding-evidence.mjs STATE_DIRECTORY PROBE_DESCRIPTOR_FILE EVIDENCE_FILE
```

The command installs `coding-certification.json` and records its canonical SHA-256 in `runtime.json` as `coding_evidence_sha256`.
Restart the runner, then request a fresh setup probe through the existing control plane.
The current grant and configuration checks still apply. Coding readiness requires the installed evidence and successful current capability probes.
This installation does not send an enable request or change backend profile activation behavior.
Task 9 owns the separately planned activation changes. Task 11 still owns release-wide real-provider acceptance.
