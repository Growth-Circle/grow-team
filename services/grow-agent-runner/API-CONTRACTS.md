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
