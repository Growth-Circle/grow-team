# Grow Team MCP access

The user authorizes implementation, tests, and production deployment.

Expose the workspace through `/mcp` with Streamable HTTP and JSON responses.
Support protocol versions 2024-11-05, 2025-03-26, 2025-06-18, and 2025-11-25.
Use OAuth discovery, dynamic public client registration, authorization codes, S256 PKCE, and rotating refresh tokens.
Show consent after workspace login at `/mcp/authorize`. Require CSRF protection for approval and rejection.
Bind clients, grants, codes, and tokens to the current realm and canonical resource URL.
Store only token and code hashes. Reject expired, consumed, revoked, inactive-user, and inactive-realm credentials.

Provide personal, revocable agent tokens through the existing `#mcp` dashboard.
Default each grant to `team:read`. Permit `team:write` only after explicit consent.
Use existing Zulip views for channel, topic, message, and task access.
Provide `search` and `fetch` tools for ChatGPT, plus channel, message, and task tools for other clients.
Keep outbound MCP catalog models separate from inbound workspace grants.
Log tool names and outcomes without message content, arguments, or credentials.

Test OAuth discovery, consent, PKCE, replay, refresh rotation, tenant boundaries, token revocation, and permission checks.
Build on rama-tuf. Back up production, build a new image, migrate, deploy, and check the public endpoints.
Retain the previous image and Compose configuration for recovery.
