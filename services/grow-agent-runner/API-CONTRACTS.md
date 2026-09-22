# Runner connection additions

All JSON envelopes use `schema_version: 1`. Runner routes reject browser session identity.
Existing human routes and legacy pairing-start payloads remain compatible.

| Route                                     | Request                                                                              | Response and retry contract                                                                           |
| ----------------------------------------- | ------------------------------------------------------------------------------------ | ----------------------------------------------------------------------------------------------------- |
| `POST /api/v1/agent/pairings`             | `device_name`, `fingerprint`, `polling_secret`                                       | Server-issued `user_code`, `pairing_id`, `state`, `expires_at`. No bearer credential is issued.       |
| `POST /api/v1/agent/pairings/status`      | `pairing_id`, `polling_secret`                                                       | `state`, `expires_at`, `runner_id`, `recovery`. The runner ID appears only after exchange.            |
| `POST /api/v1/agent/pairings/exchange`    | Existing payload                                                                     | Adds `expires_at` and `refresh_expires_at`. Remains single-use.                                       |
| `POST /api/v1/agent/runner/token/refresh` | Existing refresh payload                                                             | Adds `runner_id`, `expires_at`, and `refresh_expires_at`. Remains single-use.                         |
| `POST /api/v1/agent/runner/workspaces`    | `workspace_alias`, `canonical_origin`, `allowed_refs`, `required_checks`, `revision` | `repository: {id, workspace_alias, revision, policy_version}`. Exact replay returns the same binding. |
| `POST /api/v1/agent/runner/catalog`       | Existing catalog payload                                                             | Exact same-revision replay returns the existing revision without readiness invalidation.              |
| `GET /api/v1/agent/runner/leases`         | Existing bearer request                                                              | Each lease adds `event_cursor` for ordered stopped evidence.                                          |

Pairing status is rate limited. The polling secret stays in the JSON body.
Invalid IDs and secrets receive the same 400 response. No credential material is returned by status.
Pending or approved pairings report `expired` after their deadline.
Exchanged pairings retain their state and orphan identity after the pairing deadline.
`recovery` is `poll`, `re_pair`, or `re_pair_and_revoke_orphan`.

Workspace revision uses the existing repository `policy_version`.
The first revision is 1. Changed metadata requires the next revision.
The server locks the runner and repository while it checks revision and ownership.
Changed metadata invalidates dependent profile readiness. Disabled repositories cannot be revived through registration.
Host path fields are rejected. Repository URL and check validation reuse the current server rules.

Bearer failures return HTTP 401 with `code` equal to `credential_invalid`, `credential_expired`, or `credential_revoked`.
These errors disclose no token, hash, owner, or unrelated resource data.
Policy rejection remains separate from credential rejection. Contention and transient failures retain their retry status.
The stop-evidence endpoint keeps its existing revoked-token exception.

Tasks 9 and 11 can use these contracts for owner setup UI and integration tests.
A successful status or registration response does not prove runtime readiness.

## Task 7 runtime callbacks

`POST /runner/authority` accepts `LeaseRequest`. It checks the current job version and observes the existing attempt lease.
The response includes job ID, attempt ID, lease epoch, job version, and expiry.

`POST /runner/setup-authority` accepts setup ID, claim key, lease epoch, descriptor digest, and configuration digest.
It returns the same bindings, grant ID, and the earlier grant or lease expiry.
It checks current realm, runner, setup, grant, and configuration authority. It does not renew or claim anything.

Credential routes return ephemeral secrets with `Cache-Control: no-store`.
The runner never writes these responses to its journal.
Artifact upload uses multipart fields `payload` and `file`. The server returns the artifact ID, checksum, and size.
Selected attachment download uses the binary `/runner/context-file` route.
Both routes reject redirects and enforce response bounds. The runner checks connection and attempt identity after responses.

Checkpoint payloads include source attempt, input cursor, next step, context IDs, and verified server artifact IDs.
An uncertain upload cannot create a local substitute for a server receipt.
Operation proposals and consumption use the current serialized job version and the returned `operation_hash`.
