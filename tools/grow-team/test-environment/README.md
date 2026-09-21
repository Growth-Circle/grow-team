# Grow Team local agent test environment

Use this environment only for the Grow Team agent worktree.

It starts one rootless Docker PostgreSQL 14 container with PGroonga.
It uses the pinned Grow Team database image.
It listens only on `127.0.0.1:5432`.
It copies Python packages into a new venv from the local branding worktree.
It does not link to or modify that worktree.
It builds the missing LDAP extension in an ephemeral rootless container.
It stores the matching OpenLDAP runtime beside this test environment.
It stores the required libvips runtime beside this test environment.

Run these commands from the repository root:

```bash
tools/grow-team/test-environment/setup-dependencies.sh
tools/grow-team/test-environment/bootstrap.sh
tools/grow-team/test-environment/run.sh tools/test-backend --parallel=1 zerver.tests.test_users.PermissionTest.test_get_admin_users
tools/grow-team/test-environment/run.sh tools/test-js-with-node util
tools/grow-team/test-environment/run.sh tools/webpack --quiet
```

The bootstrap copies reused generated assets from the branding worktree.
Use them only for local test setup.
Run the upstream fresh asset build before a production artifact or release.

For browser tests, start the additional cache services:

```bash
tools/grow-team/test-environment/browser-services.sh
```

This command starts only Redis and memcached in the same rootless Docker project.
It binds ports `6379` and `11211` to loopback and rejects ports used by other services.
It does not restart or reset the test database.
Redis uses the optional development password through a private, ignored configuration file.
Without that setting, it accepts passwordless connections on the test loopback port.
Both caches are temporary and have no persistent data volume.

Keep Puppeteer's browser cache inside this worktree:

```bash
export PUPPETEER_CACHE_DIR="$PWD/tools/grow-team/test-environment/.state/puppeteer"
export PUPPETEER_SKIP_CHROME_HEADLESS_SHELL_DOWNLOAD=true
export PUPPETEER_SKIP_FIREFOX_DOWNLOAD=true
```

Do not run browser and backend database tests concurrently.
The upstream browser harness resets the isolated `zulip_test` database between tests.
Run the application browser suite after the current migrations and test template match.

The bootstrap script creates these ignored local files:

- `.venv/` and `node_modules/` in this worktree.
- `zproject/dev-secrets.conf` with generated local test secrets and database credentials.
- `tools/grow-team/test-environment/.env` with generated container passwords.
- `tools/grow-team/test-environment/.wheelhouse/` with the local LDAP wheel.
- `tools/grow-team/test-environment/.runtime-lib/` with OpenLDAP 2.5 shared libraries.
- `tools/grow-team/test-environment/.runtime-vips-lib/` with libvips dependencies missing from the host.
- `tools/grow-team/test-environment/.runtime-vips-host-libs/` with container libraries that an exact host soname replaces.
- `var/` test fixtures and generated test assets.

The test database helper needs `/home/ramaaditya/Project/.worktrees/.zulip-dev-uuid`.
Create the shared marker once when it is absent.
Do not overwrite an existing marker.

Do not use `tools/setup/postgresql-init-test-db` or `tools/rebuild-test-database`.
Those scripts require host PostgreSQL, host `psql`, `sudo`, `~/.pgpass`, and memcached.
The Python lock builds `psycopg2` from source. This host has no `pg_config`.
The setup script uses the binary payload from `psycopg2-binary==2.9.12` only in the isolated test venv.
The setup script installs locked `lxml==6.1.0` and `xmlsec==1.3.17` wheels only in this test venv.
The lock forces source builds, but the host lacks `libxml2` and `libxslt` headers.
Do not mount a Docker socket, user home directory, or production configuration into a runner sandbox.

For runner subprocess tests, use a dedicated rootless container.
Test a fake ACP child process for deterministic unit and integration checks.
Certify each installed and approved ACP adapter separately before production use.
Assert timeout, cancellation, process-group cleanup, read-only workspace mounts, no network, and resource limits.
Use `bwrap` only for small local mount and process-tree helper tests.
## Upstream Puppeteer harness

Run this preflight before an upstream browser test.

```bash
tools/grow-team/test-environment/puppeteer-harness.sh --preflight
```

The preflight creates a private `psql` wrapper below `.state/`. It uses only
the `grow-team-agent-test-database-1` container. It does not install host
packages or modify PostgreSQL roles, databases, or services.

The upstream harness resets `zulip_test` after every browser test. Wait for a
database handoff before this command.

```bash
GROW_TEAM_BROWSER_DB_HANDOFF=1 \
  tools/grow-team/test-environment/puppeteer-harness.sh --run login.test.ts
```
