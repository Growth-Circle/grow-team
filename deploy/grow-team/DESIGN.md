# Grow Team internal deployment

Date: 2026-09-21

## Approved scope

Deploy Zulip for Rama's internal team at `https://team.growc.id`.
Use `Growth-Circle/grow-team` as the source fork.
Use `/home/ramaaditya/Project/grow-team` as the local checkout.
Use `server-gteam` as the project SSH alias.
Keep landing pages, billing, and product redesign outside this release.

## Architecture

Use stable Zulip 12.2 and the corresponding official container image.
Store deployment configuration in the fork without credentials.
Run a dedicated Docker engine and Compose project on the shared VPS.
Use separate PostgreSQL, Redis, RabbitMQ, Memcached, and application volumes.
Publish the application only on `127.0.0.1:18300`.
Reuse the existing Cloudflare tunnel for `team.growc.id`.
Keep the private model connection available for later agent integration.
Send transactional email through a protected Cloudflare Worker and its `send_email` binding.
Use Wrangler OAuth for deployment and a separate relay secret for runtime requests.
Keep MIME content and BCC privacy intact through the email backend.

## Isolation and recovery

Keep HermesTrading services, timers, application files, and data unchanged.
Limit Grow Team CPU and memory through its own systemd slice and containers.
Use the supported rootful Zulip runtime without installing Zulip into the host OS.
Archive the Buzz database, volumes, configuration, and service definitions before removal.
Keep the original HermesTrading SSH alias.

## Acceptance

The public HTTPS endpoint serves Zulip.
The owner can sign in through a browser.
Team invitations and email verification work with the configured mail provider.
Authenticated users can send messages and receive live updates.
Containers and the tunnel restart through their service configuration.
Database and file backups can be inspected and restored.
HermesTrading unit hashes remain unchanged and its timers remain enabled.
