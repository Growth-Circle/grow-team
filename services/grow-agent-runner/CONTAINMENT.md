# Linux containment API

Task 6 supplies the tool boundary. It does not enable native agents or report `code_ready`.

`RootlessSandbox.open` requires the owner-approved local Docker executable, endpoint, state directory, and immutable image IDs. The endpoint must be `unix:///run/user/<uid>/docker.sock`. The rootless engine must use seccomp and cgroup v2. The installation identity remains stable across runner credential changes.

Build the tool image offline:

```bash
docker --host unix:///run/user/$(id -u)/docker.sock build --network=none --pull=false -t grow-task6-tools:20260922 image
docker --host unix:///run/user/$(id -u)/docker.sock image inspect grow-task6-tools:20260922 --format '{{.Id}}'
npm run test:containment
```

Use the returned immutable image ID in the owner catalog. The base is pinned in `image/Dockerfile`. This image supplies Node and the file-tool helper. It does not supply repository-specific dependencies. A missing executable fails its check.

## Consumer contracts

1. Validate the frozen descriptor with Task 5. Resolve its source through `OwnerRegistry.assertWorkspace`.
2. Resolve the allowed base ref locally. Supply its fixed, approved commit to `prepareWorkspace`.
3. Supply a current lease callback. It must throw after disconnect, cancellation, expiry, or epoch replacement.
4. Publish the returned `workspace.prepared` record before any tool operation.
5. Construct `ExecutionGuard` with the current callback, a hard deadline, and an `AbortSignal`.
6. Construct `ToolBroker` with the existing `AttemptChannel` and its `OperationBoundary`.
7. Implement `BrokerPersistence.upload` with `/runner/artifacts`. Return its `artifact_id`, `checksum`, and `size`.
8. Implement `publishTree` with the existing server checkpoint operation. Include the current input cursor and required checkpoint fields.
9. Use one artifact directory across resumed attempts. Construct its limits from `artifact_bytes` and `job_artifact_bytes`.
10. Call `verifyFinalTree` before submitting results. Call `assertVerified` before any later Git effect.

The broker persists local intent before operation proposal and consumption. It uses the consumed `operation_hash` in wire tool events. Lost consumption prevents execution. Repeated tool IDs require reconciliation. Server artifact IDs remain distinct from local artifact IDs. Uploaded checksum and size must match the retained bytes.

An edit or writable shell operation clears the server tree. `publishTree` must finish before another operation. A local snapshot alone cannot authorize checks. Checks use the exact owner-approved argv, cwd, and timeout. They mount the final tree read-only. Checks that require generated files must use `/tmp` or an approved offline toolchain design.

`stopAttempt` remains usable after lease revocation. It freezes new launches, waits for pending launches, and confirms process containment. An inspection or stop failure throws. The consumer must report interrupted state and retain the journal. It must not report cancelled or completed.

For restart recovery, call `inspect` and stop each returned scope before accepting work. Container and volume labels bind resources to one installation. Stop operations also verify immutable container IDs and scope labels. PID observations include kernel start times.

## Probe authority

`runProbe` requires a separate `ProbeAuthority`: `setup_operation_id`, `assertCurrent`, `deadline`, and `signal`. It uses a distinct `probe-<setup_operation_id>` scope. It mounts no project or provider socket. It has the same resource limits and watchdog.

Task 7 must supply the real setup grant and control revocation checks. The bare Task 5 `Supervisor.probe(descriptor)` interface does not authorize process execution. Do not synthesize a job lease for setup. Task 7 must also integrate provider request revocation and the separate model container.

## Limits and retained evidence

The working tree has a 32 MiB limit and at most 20,000 regular files. Preparation rejects symlinks, gitlinks, special files, credentialed origins, and sensitive paths. Git metadata stays outside every project container. The checkout uses its own objects and index. Host inspection disables hooks, helpers, filters, lazy fetch, and replacement objects.

Writable tools use a labeled, bounded tmpfs volume. The seed mount is read-only. The broker pauses the container before bounded tar export. It accepts regular files and directories only. It rejects unsafe paths, links, duplicates, and unsupported tar extensions. Each accepted export creates a separate retained host snapshot. Export failure leaves the previous snapshot and an uncertain operation.

`temporary_bytes` covers `/tmp` and a 64 KiB shared-memory mount. Memory, swap, CPU, process count, output, file size, and elapsed time have explicit limits. Output uses the descriptor limit, up to 50 KiB. Overflow stops the tool and cannot pass verification. Shell timeouts remain capped by the descriptor. Owner checks can use their approved timeout, up to 20 minutes.

The independent watchdog runs through a scoped user systemd service. It uses monotonic deadlines, supervisor PID start time, and short heartbeats. It discovers owned running orphans. It survives supervisor death and restarts after its own process exits. Engine access failure prevents a confirmed stop. The implementation does not change the Docker service or reboot the host.

The broker retains stopped containers, volume metadata, snapshots, journals, and checksummed artifacts. Tmpfs contents disappear after unmount. Volume metadata is not a recoverable snapshot. No automatic prune or deletion runs.

This profile currently accepts SHA-1 Git repositories. Sensitive path rules are conservative, including `.env` templates and native-agent configuration directories. Unsupported repositories fail before execution. Native model images, provider relay tests, remote Git effects, and deployment remain separate tasks.
