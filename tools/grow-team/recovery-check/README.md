# Isolated recovery rehearsal

Run `tools/grow-team/recovery-check/run.sh` from the worktree root.

Each run creates source and target PostgreSQL containers with a generated
invocation ID. The ID also names its private network, volumes, state directory,
database names, and two loopback ports. The script never uses `zulip_test` or any
existing database.

The script creates one disposable source database and one empty target database.
It migrates the source, creates nontrivial synthetic agent records, runs the real
`manage.py backup` command, restores its PostgreSQL directory dump, and verifies
foreign keys, statuses, artifact checksums, and v1/v2 secret decryption.

The PostgreSQL client exists only in a child Bubblewrap mount namespace. That
client is a pinned container proxy because the host has no PostgreSQL 14 client.
The script requires the current user's explicit local rootless Docker endpoint at
`unix:///run/user/<uid>/docker.sock`. It rejects a remote or conflicting context.
The private state directory is under `.runs/<invocation-id>/`. It contains
generated credentials, keyring material, archives, and evidence. Do not commit,
print, copy, or reset that directory.

Each invocation is single-use. A later run creates a new invocation ID and does
not reuse a prior target. The script checks that the new target has no user
relations before it restores. It stops only the two container IDs that it created
and recorded. It keeps its labeled containers, volumes, and network for inspection.
Remove nothing without separate explicit authorization.
