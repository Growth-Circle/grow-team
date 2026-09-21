# Grow Team deployment implementation plan

**Goal:** Replace Buzz with a working Zulip web workspace for the internal team.

**Architecture:** Use stable Zulip containers behind the existing Cloudflare tunnel.
Keep all application state separate from HermesTrading.

**Tech Stack:** Zulip 12.2, Docker Compose, PostgreSQL, Redis, RabbitMQ, Memcached, systemd, Cloudflare Tunnel.

**Spec:** `DESIGN.md`

## Constraints

- Use `Growth-Circle/grow-team`.
- Use `team.growc.id`.
- Preserve HermesTrading.
- Keep credentials outside Git.
- Keep recovery archives before removing Buzz.
- End each commit with the required CADIS trailer.

## Execution

- [x] Inspect the VPS, SSH aliases, existing deployment, and official Zulip deployment instructions.
- [x] Create the fork and rename the RD-Team SSH alias.
- [x] Record HermesTrading unit hashes and timer state.
- [x] Back up Buzz and remove its running application containers and agent.
- [x] Check out Zulip 12.2 and add deployment files to the fork.
- [x] Start the dedicated Docker engine with resource limits.
- [x] Validate Compose configuration and initialize Zulip.
- [x] Configure outgoing email, the organization, owner account, and team invitation.
- [x] Move the existing Cloudflare connector to the Grow Team service.
- [x] Test public HTTPS, sign-in, invitations, messages, live events, and backups.
- [x] Verify HermesTrading against the recorded baseline.
- [ ] Commit and push the deployment configuration and operations guide.
- [ ] Deliver the workspace link and private account access instructions.
