# Grow Agent runner foundation

The runner connects to Grow Team through outbound HTTPS requests. It opens no inbound port.
This package uses a separate npm lockfile. It does not use the browser dependency tree.

The current distribution supports Linux x64 with Node 24.18.0.
Tasks 6–8 must provide the sandbox, runtime, and Git harness.
The foundation does not certify chat or code execution. `doctor` reports no certified modes.

## Development

Use Node 24.18.0. Run these commands from this directory:

```sh
npm ci --ignore-scripts
npm test
node dist/cli.js doctor
node dist/cli.js help
```

The lockfile pins ACP SDK 1.5.0, Codex ACP 1.12.0, Codex 0.154.0, and Zod 4.6.5.
It also pins the Linux x64 Codex payload and compiler dependencies.
`npm ci` verifies package archive integrity. Tests use the built JavaScript.

## Connect a device

```sh
node dist/cli.js connect https://grow.example
```

Approve the returned pairing ID and user code in the authenticated Grow Team interface.
Run `node dist/cli.js status` after approval. It exchanges the polling secret once.
The server issues the user code. The CLI never displays the polling secret or bearer credentials.

State defaults to `~/.local/state/grow-agent`. Set `GROW_AGENT_STATE` before startup to select another directory.
Use an owner-only directory. Directory permissions are 0700; file permissions are 0600.
Writes use a new file, file synchronization, atomic rename, and directory synchronization.
Do not copy an active state directory to another running supervisor.

The control-plane origin becomes fixed at connection. URL credentials, URL paths, queries, and fragments are rejected.
Public origins require HTTPS. Explicit loopback origins can use HTTP for development.
Every redirect is rejected. Requests stop after 25 seconds and have bounded bodies.

`rotate` replaces access and refresh credentials in one private atomic write.
Automatic rotation uses the actual server expiry. Revoked credentials never trigger automatic pairing.
A lost exchange response leaves an uncertain exchange. `status` reports the orphan runner ID when the server has one.
A lost rotation response leaves `rotation_uncertain`. The runner does not replay rotation or revive the old token.
Use `connect ORIGIN --repair` for an explicit new authorization. Revoke the reported orphan through its owner interface.
An uncertain pairing start also requires this explicit action. No runner exists before exchange.

## Owner configuration

Register a local workspace with an explicit CLI command:

```sh
node dist/cli.js workspace app /absolute/local/path workspace.json
```

Example `workspace.json`:

```json
{
  "canonical_origin": "https://example.com/team/app.git",
  "allowed_refs": ["main"],
  "required_checks": [{"id": "test", "argv": ["npm", "test"], "cwd": ".", "timeout_seconds": 120}],
  "revision": 1
}
```

Only alias, origin, allowed refs, checks, and revision go to the server.
The local path stays in `registry.json`. The runtime must check this mapping before execution.
An exact retry preserves the repository ID. Change metadata with the next revision.
The server derives owner, realm, and runner from the bearer credential.

Use `catalog CATALOG.json` to approve a typed adapter and sandbox catalog.
Catalogs contain adapter identities and pinned image/toolchain digests. They do not contain executable commands.
The owner supplies each revision. An exact replay preserves readiness; changed metadata requires a newer revision.
Browser profile settings cannot create local commands or host paths.

Use `secret NAME FILE` to register an owner-local secret reference.
The file must have owner-only permissions. The registry stores its path, not its contents.
`resolveSecret` reads the secret into memory only. Ordinary journal records reject credential fields.
The later runtime broker must redact provider credentials from event text and tool output.
Do not put provider credentials in commands, metadata, events, or diagnostic text.

## Durable transport and execution boundary

The SQLite journal enables WAL, FULL synchronization, foreign keys, and a two-second busy bound.
A separate SQLite exclusive transaction permits only one supervisor per state directory.
Process death releases ownership without a stale PID lock. The journal has no automatic destructive repair.

Persist each request before sending it. Preserve claim keys, event IDs, input IDs, operation IDs, hashes, versions, and nonces.
A request becomes uncertain before transmission. A response becomes complete only after its receipt is durable.
The transport replays only the original request. It does not manufacture revisions or retry identities.
Journal records and server registry bindings belong to one runner identity.
Explicit re-pair preserves old connection and registry snapshots in private history files.
It preserves old journal partitions, local workspace mappings, and secret references.
It requires new catalog and workspace reports before runtime use.
Old requests cannot use new runner credentials. History files never restore credentials automatically.

`Coordinator` consumes a `Supervisor` and an owner registry. Tasks 6–8 must implement the interface in `src/supervisor.ts`.
`inspect` must enumerate all owned processes and containers, including unknown journal entries.
`stop` must confirm that effects have stopped. Recovery stops host processes before credential checks or server requests.
It recovers pending claim identities, reconciles server leases, and reports stopped evidence before new claims.
The server lease cursor permits ordered cleanup after a lost event response.

`start` must return after process launch. It must not apply descriptor inputs itself.
Input delivery goes through `applyInput`. The journal fences each input before calling the runtime.
An uncertain input requires receipt reconciliation. It must not be delivered again.
`reconcileInput(attemptId, input, receipt)` uses the original durable attempt and epoch after stop or restart.
It does not require or restore an execution lease.
The input receipt route accepts `job_version` for compatibility, but does not apply a version comparison.
The server still checks runner identity, current access, input order, and the latest attempt.
Each attempt has one immutable input outcome. The server preserves previous outcomes in append-only receipt history.
An `applied` receipt prevents duplicate application. A `not_applied` receipt clears uncertainty for owner recovery.
The same attempt cannot apply that input again. A redelivery request stops that attempt and requires a fresh owner-authorized attempt.
Events also enter the journal before transport. A lost acknowledgement replays the same event before later events.
Each runtime channel holds one immutable attempt session. Retired channels cannot access a later attempt.
Queued callbacks and delayed responses check the original session before further use.

An operation must use `propose`, `consume`, `beginEffect`, and `finishEffect` in that order.
`consume` checks the returned operation identity and hash. The server returns status `started` after consume.
`beginEffect` checks current lease identity and records uncertainty before an effect.
A lost consume response never creates execution permission. Query server operations and reconcile the durable local record.
A restarted effect cannot run again from its original consume receipt.
Remote effects require server receipt reconciliation before reporting success.

The trusted host uses `Coordinator.operationRecovery(attemptId)` after stop or restart.
It exposes only `list`, `remoteReceipt`, and `localReceipt`. It does not expose execution methods to retired channels.
The interface binds requests to the original journaled runner, job, attempt, epoch, and attempted consume identity.
A lost receipt response reuses the original durable request. Acknowledged recovery completes uncertain local effect evidence.
Existing effect receipts remain unchanged. Recovery does not turn an uncertain consume into execution permission.

The operation list, remote receipt, and local receipt routes retain `job_version` for request compatibility.
These three recovery routes do not use it as a version gate.
Current access and audience checks still apply. Remote receipts require consumed approval and the exact approved target.
Local receipts require the stopped latest attempt and a valid workspace observation.
All receipts remain immutable. Recovery does not resume a job or authorize another effect.

Idle polling sends an empty-lease heartbeat after containment and credential checks. Presence does not certify runtime readiness.
The coordinator checks controls, heartbeats, and lease expiry. A separate expiry timer stops effects during a blocked request.
Transport or credential failure confirms local containment before polling backoff or reconnect. Current authority must still be checked at each broker effect.
Reserved environment values are not inherited. Only the fixed owner-approved environment allowlist reaches adapters.
The later runtime supplies isolated HOME and credential paths through its own supervisor boundary.

The current `run` driver has no execution adapter. It cannot claim new work or certify runtime readiness.
It refuses recovery when old local attempts require unavailable runtime inspection.
A live service is not evidence that a profile is ready.

## Owner remote and Titen configuration

Register each credential as an owner-local secret reference. Do not put credential values in configuration files.

```sh
grow-agent secret github-token /secure/path/github-token
grow-agent secret titen-token /secure/path/titen-token
grow-agent remote remote-operations.json
grow-agent titen titen.json
```

`remote-operations.json` contains exact repository and provider bindings:

```json
{
  "git": [{"repository_id": "REPOSITORY_ID", "remote": "https://github.com/owner/repo.git", "credential_secret_ref": "github-token"}],
  "github": [{"remote": "https://github.com/owner/repo.git", "api_base": "https://api.github.com", "credential_secret_ref": "github-token"}]
}
```

`titen.json` maps each immutable requester to one owner-approved stable subject:

```json
{
  "endpoint": "https://memory.example/mcp",
  "credential_secret_ref": "titen-token",
  "subjects": [{"control_origin": "https://control.example", "realm_id": 1, "requester_user_id": 2, "subject_id": "person:example"}]
}
```

`grow-agent run` resolves the lowercase Git origin and performs one bounded Titen compile. It does not pass a visibility argument to compile. It adds returned text as untrusted context. A missing configuration, an originless repository, or a failed memory request adds no context. Patch-only jobs still run.

The host creates the candidate from the verified tree. It proposes the exact push and draft PR operations. It waits for approval with controls active. It uses one explicit expected-head lease and reconciles a lost receipt before another effect. The model and project containers do not receive remote Git or Titen credentials.

Use `grow-agent memory-signal SIGNAL.json` only for a typed, verified, owner-approved signal. The signal must include an owner-only evidence path, its SHA-256, a stable idempotency key, and immutable audience fields. The command verifies the evidence, uses the configured requester subject, then calls canonical `titen_remember` and `titen_consolidate` with organization visibility. It does not accept model input, prompts, transcripts, secrets, recalled memory, or routine tool output as durable signals.

## User service

Place the assembled directory at `~/.local/lib/grow-agent`.
Copy `systemd/grow-agent.service` to `~/.config/systemd/user/`.
Then run:

```sh
systemctl --user daemon-reload
systemctl --user enable --now grow-agent.service
```

The service runs under the owner account, uses umask 0077, and opens no inbound port.
Do not enable it for coding work until the runtime and sandbox gates pass.

## Assemble a local distribution

```sh
npm run build
npm run assemble
```

Assembly creates a new `release/linux-x64-TIMESTAMP` directory. It never overwrites an earlier distribution.
It installs the complete locked production dependency tree, including the complete platform payload.
It includes Node, JavaScript, schemas, source notices, licenses, and a generated CycloneDX package SBOM.
`release-manifest.json` records package integrity and file SHA-256 values.

The source bundle includes the pinned Codex LICENSE and NOTICE, complete Bubblewrap and wrapper sources, and vendor notices.
Original copyright and source notices remain intact.
Native embedded dependencies still require SBOM review before external redistribution.
Zsh requires the supported Linux base libraries, including `libtinfo.so.6`, `libm.so.6`, and `libc.so.6`.
Tasks 11–12 retain acceptance, recovery, certification, and pilot release gates.
