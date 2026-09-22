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
The runtime does not report real coding certification from synthetic fixtures.

## Authority and containment

The host broker owns provider credentials. The model container gets no provider, runner, or Git credential.
Each model container has network disabled, a read-only root, fixed resource limits, and no project mount.
A single read-only Unix socket mount carries model and tool requests to the host broker.
The private socket parent stays outside the container. The tool container has no socket mount.
Socket ownership records remain in the runner state directory. Private socket directories remain below `/tmp/grow-broker-*`.
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

Native session load and active steering remain unsupported until certification.
Both modes use checkpoint data for a fresh session. Checkpoint text has no authority.
Context recovery preserves the active input once. It does not replay uncertain tools.
New live input receives `not_applied`; the caller must stop and reconcile before a fresh attempt.
Hard monetary caps fail closed until reviewed pricing reservations exist.
Token reservations use conservative input byte counts and fixed output ceilings.
An uncertain provider request retains its reservation. Only explicit 429 or 5xx responses permit bounded retries.

Run isolated integration checks with the reviewed image tags:

```bash
npm run test:runtime
```

This suite uses synthetic providers and real rootless containers. It makes no real model calls.
