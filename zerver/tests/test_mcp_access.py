import base64
import hashlib
import json
from urllib.parse import parse_qs, urlsplit

from django.db import connection
from django.http import HttpResponse
from django.test import Client
from typing_extensions import override

from zerver.lib.test_classes import ZulipTestCase


class MCPAccessTests(ZulipTestCase):
    @override
    def setUp(self) -> None:
        super().setUp()
        self.user = self.example_user("hamlet")
        self.login_user(self.user)
        self.resource = "http://zulip.testserver/mcp"
        self.verifier = "a" * 64
        self.challenge = (
            base64.urlsafe_b64encode(hashlib.sha256(self.verifier.encode()).digest())
            .rstrip(b"=")
            .decode()
        )

    def register(self) -> dict[str, object]:
        response = self.client_post(
            "/mcp/register",
            json.dumps(
                {
                    "client_name": "Claude Cowork",
                    "redirect_uris": ["https://claude.ai/callback"],
                    "token_endpoint_auth_method": "none",
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 201)
        return response.json()

    def authorize(
        self, *, scope: str = "team:read", allow_write: bool = False
    ) -> tuple[dict[str, object], str]:
        client = self.register()
        params = {
            "client_id": client["client_id"],
            "redirect_uri": "https://claude.ai/callback",
            "response_type": "code",
            "code_challenge": self.challenge,
            "code_challenge_method": "S256",
            "state": "opaque-state",
            "resource": self.resource,
            "scope": scope,
        }
        response = self.client_get("/mcp/authorize", params)
        self.assertEqual(response.status_code, 200)
        self.assertIn("Claude Cowork", response.content.decode())
        self.assertEqual(response["Referrer-Policy"], "same-origin")
        approval = {**params, "decision": "approve"}
        if allow_write:
            approval["allow_write"] = "true"
        response = self.client_post("/mcp/authorize", approval)
        self.assertEqual(response.status_code, 302)
        query = parse_qs(urlsplit(response["Location"]).query)
        self.assertEqual(query["state"], ["opaque-state"])
        return client, query["code"][0]

    def test_oauth_write_requires_the_consent_checkbox(self) -> None:
        client, code = self.authorize(allow_write=True)
        pair = self.exchange(client, code).json()
        self.assertEqual(pair["scope"], "team:read team:write")
        names = {
            tool["name"]
            for tool in self.rpc(pair["access_token"], "tools/list").json()["result"]["tools"]
        }
        self.assertIn("send_message", names)
        self.assertIn("send_direct_message", names)
        client, code = self.authorize(scope="team:read team:write", allow_write=False)
        pair = self.exchange(client, code).json()
        self.assertEqual(pair["scope"], "team:read")
        names = {
            tool["name"]
            for tool in self.rpc(pair["access_token"], "tools/list").json()["result"]["tools"]
        }
        self.assertNotIn("send_direct_message", names)

    def test_member_lookup_direct_messages_and_mentions(self) -> None:
        from zerver.models import Message, UserMessage
        from zerver.models.streams import get_stream

        recipient = self.example_user("othello")
        token = self.manual(write=True)
        response = self.rpc(
            token, "tools/call", {"name": "list_users", "arguments": {"query": "Othello"}}
        ).json()["result"]
        self.assertFalse(response["isError"])
        members = json.loads(response["content"][0]["text"])["users"]
        self.assertIn(recipient.id, {member["user_id"] for member in members})
        self.assertNotIn("email", members[0])
        self.subscribe(self.user, "Denmark")
        self.subscribe(recipient, "Denmark")
        response = self.rpc(
            token,
            "tools/call",
            {
                "name": "send_message",
                "arguments": {
                    "channel_id": get_stream("Denmark", self.user.realm).id,
                    "topic": "MCP mention",
                    "content": "Please check this task.",
                    "mention_user_ids": [recipient.id],
                },
            },
        ).json()["result"]
        self.assertFalse(response["isError"])
        message = Message.objects.get(id=json.loads(response["content"][0]["text"])["id"])
        self.assertEqual(message.sender_id, self.user.id)
        self.assertIn(f'data-user-id="{recipient.id}"', message.rendered_content)
        user_message = UserMessage.objects.get(message=message, user_profile=recipient)
        self.assertTrue(user_message.flags.mentioned)
        response = self.rpc(
            token,
            "tools/call",
            {
                "name": "send_direct_message",
                "arguments": {"recipient_user_ids": [recipient.id], "content": "MCP direct test"},
            },
        ).json()["result"]
        self.assertFalse(response["isError"])
        message = Message.objects.get(id=json.loads(response["content"][0]["text"])["id"])
        self.assertEqual(message.sender_id, self.user.id)
        self.assertTrue(
            UserMessage.objects.filter(message=message, user_profile=recipient).exists()
        )
        denied = self.rpc(
            self.manual(),
            "tools/call",
            {
                "name": "send_direct_message",
                "arguments": {"recipient_user_ids": [recipient.id], "content": "Denied"},
            },
        ).json()["result"]
        self.assertTrue(denied["isError"])

    def test_mentions_and_direct_recipients_cannot_cross_realms(self) -> None:
        foreign = self.mit_user("sipbtest")
        token = self.manual(write=True)
        response = self.rpc(
            token,
            "tools/call",
            {
                "name": "send_direct_message",
                "arguments": {"recipient_user_ids": [foreign.id], "content": "Denied"},
            },
        ).json()["result"]
        self.assertTrue(response["isError"])
        self.subscribe(self.user, "Denmark")
        from zerver.models.streams import get_stream

        response = self.rpc(
            token,
            "tools/call",
            {
                "name": "send_message",
                "arguments": {
                    "channel_id": get_stream("Denmark", self.user.realm).id,
                    "topic": "Denied",
                    "content": "Denied",
                    "mention_user_ids": [foreign.id],
                },
            },
        ).json()["result"]
        self.assertTrue(response["isError"])

    def exchange(
        self, client: dict[str, object], code: str, verifier: str | None = None
    ) -> HttpResponse:
        return self.client_post(
            "/mcp/token",
            {
                "grant_type": "authorization_code",
                "client_id": client["client_id"],
                "code": code,
                "redirect_uri": "https://claude.ai/callback",
                "code_verifier": verifier or self.verifier,
                "resource": self.resource,
            },
        )

    def manual(self, *, write: bool = False) -> str:
        response = self.client_post(
            "/json/mcp/access", {"name": "Hermes", "write": "true" if write else "false"}
        )
        self.assertEqual(response.status_code, 200)
        return response.json()["token"]

    def rpc(self, token: str, method: str, params: dict[str, object] | None = None) -> HttpResponse:
        return self.client_post(
            "/mcp",
            json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}}),
            content_type="application/json",
            HTTP_AUTHORIZATION="Bearer " + token,
        )

    def test_discovery_and_bearer_challenge(self) -> None:
        metadata = self.client_get("/.well-known/oauth-protected-resource/mcp")
        self.assertEqual(metadata.status_code, 200)
        self.assertEqual(metadata.json()["resource"], self.resource)
        auth = self.client_get("/.well-known/oauth-authorization-server").json()
        self.assertIn("S256", auth["code_challenge_methods_supported"])
        response = self.client_post("/mcp", "{}", content_type="application/json")
        self.assertEqual(response.status_code, 401)
        self.assertIn("resource_metadata", response["WWW-Authenticate"])

    def test_oauth_pkce_code_replay_and_refresh_rotation(self) -> None:
        client, code = self.authorize()
        self.assertEqual(self.exchange(client, code, "b" * 64).status_code, 400)
        response = self.exchange(client, code)
        self.assertEqual(response.status_code, 200)
        tokens = response.json()
        self.assertEqual(self.exchange(client, code).status_code, 400)
        self.assertEqual(
            self.rpc(
                tokens["access_token"],
                "initialize",
                {
                    "protocolVersion": "2025-11-25",
                    "clientInfo": {"name": "test", "version": "1"},
                    "capabilities": {},
                },
            ).status_code,
            200,
        )
        refresh = {
            "grant_type": "refresh_token",
            "client_id": client["client_id"],
            "refresh_token": tokens["refresh_token"],
            "resource": self.resource,
        }
        rotated = self.client_post("/mcp/token", refresh)
        self.assertEqual(rotated.status_code, 200)
        self.assertNotEqual(rotated.json()["refresh_token"], tokens["refresh_token"])
        self.assertEqual(self.client_post("/mcp/token", refresh).status_code, 400)
        self.assertEqual(self.rpc(rotated.json()["access_token"], "tools/list").status_code, 401)

    def test_dashboard_revocation_and_default_read_scope(self) -> None:
        token = self.manual()
        tools = self.rpc(token, "tools/list").json()["result"]["tools"]
        names = {tool["name"] for tool in tools}
        self.assertIn("search", names)
        self.assertIn("fetch", names)
        self.assertNotIn("send_message", names)
        response = self.rpc(
            token,
            "tools/call",
            {
                "name": "send_message",
                "arguments": {"channel_id": 1, "topic": "test", "content": "denied"},
            },
        )
        self.assertTrue(response.json()["result"]["isError"])
        connections = self.client_get("/json/mcp/access").json()["connections"]
        self.assertEqual(len(connections), 1)
        self.assertNotIn("token", connections[0])
        response = self.client_post("/json/mcp/access/" + connections[0]["id"] + "/revoke")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.rpc(token, "tools/list").status_code, 401)

    def test_read_tools_use_user_permissions(self) -> None:
        self.subscribe(self.user, "Denmark")
        message_id = self.send_stream_message(
            self.user, "Denmark", "Visible MCP message", topic_name="MCP"
        )
        token = self.manual()
        response = self.rpc(
            token, "tools/call", {"name": "fetch", "arguments": {"id": f"message:{message_id}"}}
        )
        self.assertFalse(response.json()["result"].get("isError", False))
        self.assertIn("Visible MCP message", response.content.decode())
        private_id = self.send_personal_message(
            self.example_user("othello"), self.example_user("iago"), "Hidden MCP message"
        )
        response = self.rpc(
            token, "tools/call", {"name": "fetch", "arguments": {"id": f"message:{private_id}"}}
        )
        self.assertTrue(response.json()["result"]["isError"])
        self.assertNotIn("Hidden MCP message", response.content.decode())

    def test_redirect_resource_and_scope_validation(self) -> None:
        client = self.register()
        params = {
            "client_id": client["client_id"],
            "redirect_uri": "https://attacker.test/callback",
            "response_type": "code",
            "code_challenge": self.challenge,
            "code_challenge_method": "S256",
        }
        response = self.client_get("/mcp/authorize", params)
        self.assertEqual(response.status_code, 400)
        self.assertNotIn("Location", response)
        params["redirect_uri"] = "https://claude.ai/callback"
        params["resource"] = "https://attacker.test/mcp"
        self.assertEqual(self.client_get("/mcp/authorize", params).status_code, 400)
        params["resource"] = self.resource
        params["scope"] = "admin"
        self.assertEqual(self.client_get("/mcp/authorize", params).status_code, 400)

    def test_registration_rejects_unsafe_redirects(self) -> None:
        for uri in [
            "javascript:alert(1)",
            "http://example.com/callback",
            "https://user:pass@example.com/cb",
            "https://example.com/cb#fragment",
        ]:
            response = self.client_post(
                "/mcp/register",
                json.dumps({"redirect_uris": [uri]}),
                content_type="application/json",
            )
            self.assertEqual(response.status_code, 400)

    def test_consent_requires_login_and_csrf(self) -> None:
        client = self.register()
        params = {
            "client_id": client["client_id"],
            "redirect_uri": "https://claude.ai/callback",
            "response_type": "code",
            "code_challenge": self.challenge,
            "code_challenge_method": "S256",
            "decision": "approve",
        }
        anonymous = Client(enforce_csrf_checks=True)
        self.assertEqual(anonymous.get("/mcp/authorize", params).status_code, 302)
        self.assertEqual(anonymous.post("/mcp/authorize", params).status_code, 403)

    def test_write_tools_are_explicit_and_bound_to_user(self) -> None:
        token = self.manual(write=True)
        self.subscribe(self.user, "Denmark")
        from zerver.models.streams import get_stream

        channel = get_stream("Denmark", self.user.realm)
        response = self.rpc(
            token,
            "tools/call",
            {
                "name": "send_message",
                "arguments": {
                    "channel_id": channel.id,
                    "topic": "MCP",
                    "content": "Approved MCP message",
                },
            },
        )
        self.assertFalse(response.json()["result"].get("isError", False))
        self.assertEqual(self.get_last_message().sender_id, self.user.id)

    def test_foreign_realm_and_inactive_users_cannot_use_tokens(self) -> None:
        from zerver.models.mcp_access import MCPAccessGrant

        token = self.manual()
        response = self.client_post(
            "/mcp",
            json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}),
            content_type="application/json",
            HTTP_AUTHORIZATION="Bearer " + token,
            subdomain="zephyr",
        )
        self.assertEqual(response.status_code, 401)
        self.user.is_active = False
        self.user.save(update_fields=["is_active"])
        self.assertEqual(self.rpc(token, "tools/list").status_code, 401)
        grant = MCPAccessGrant.objects.get(user=self.user)
        self.assertIsNone(grant.last_used_at)

    def test_connection_management_is_personal(self) -> None:
        self.manual()
        grant_id = self.client_get("/json/mcp/access").json()["connections"][0]["id"]
        self.login_user(self.example_user("othello"))
        self.assertEqual(self.client_get("/json/mcp/access").json()["connections"], [])
        self.assertEqual(self.client_post(f"/json/mcp/access/{grant_id}/revoke").status_code, 404)

    def test_refresh_cannot_change_scope_or_resource(self) -> None:
        client, code = self.authorize()
        tokens = self.exchange(client, code).json()
        params = {
            "grant_type": "refresh_token",
            "client_id": client["client_id"],
            "refresh_token": tokens["refresh_token"],
            "scope": "team:read team:write",
        }
        self.assertEqual(self.client_post("/mcp/token", params).status_code, 400)
        params.pop("scope")
        params["resource"] = "https://other.test/mcp"
        self.assertEqual(self.client_post("/mcp/token", params).status_code, 400)
        params["resource"] = self.resource
        self.assertEqual(self.client_post("/mcp/token", params).status_code, 200)

    def test_expired_codes_tokens_and_grants_are_rejected(self) -> None:
        from datetime import timedelta

        from django.utils.timezone import now

        from zerver.models.mcp_access import MCPAccessGrant, MCPAccessToken, MCPAuthorizationCode

        client, code = self.authorize()
        MCPAuthorizationCode.objects.update(expires_at=now() - timedelta(seconds=1))
        self.assertEqual(self.exchange(client, code).status_code, 400)
        token = self.manual()
        MCPAccessToken.objects.update(expires_at=now() - timedelta(seconds=1))
        self.assertEqual(self.rpc(token, "tools/list").status_code, 401)
        token = self.manual()
        MCPAccessGrant.objects.update(expires_at=now() - timedelta(seconds=1))
        self.assertEqual(self.rpc(token, "tools/list").status_code, 401)

    def test_transport_notifications_origins_and_argument_limits(self) -> None:
        token = self.manual()
        response = self.client_post(
            "/mcp",
            json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}),
            content_type="application/json",
            HTTP_AUTHORIZATION="Bearer " + token,
        )
        self.assertEqual(response.status_code, 202)
        response = self.client_post(
            "/mcp",
            json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}),
            content_type="application/json",
            HTTP_AUTHORIZATION="Bearer " + token,
            HTTP_ORIGIN="https://attacker.test",
        )
        self.assertEqual(response.status_code, 403)
        response = self.rpc(
            token,
            "tools/call",
            {"name": "get_messages", "arguments": {"channel_id": 1, "limit": 101}},
        )
        self.assertTrue(response.json()["result"]["isError"])
        response = self.rpc(
            token, "tools/call", {"name": "get_messages", "arguments": {"channel_id": True}}
        )
        self.assertTrue(response.json()["result"]["isError"])
        response = self.rpc(
            token, "tools/call", {"name": "list_channels", "arguments": {"include_all": True}}
        )
        self.assertTrue(response.json()["result"]["isError"])

    def test_search_topics_messages_and_tasks(self) -> None:
        from zerver.models.streams import get_stream

        self.subscribe(self.user, "Denmark")
        channel = get_stream("Denmark", self.user.realm)
        self.send_stream_message(
            self.user, "Denmark", "Unique mcp regression phrase", topic_name="MCP"
        )
        with connection.cursor() as cursor:
            cursor.execute(
                "UPDATE zerver_message SET search_tsvector = to_tsvector('zulip.english_us_search', subject || ' ' || rendered_content), search_pgroonga = escape_html(subject) || ' ' || rendered_content"
            )
        token = self.manual(write=True)
        for name, args in [
            ("list_channels", {}),
            ("list_topics", {"channel_id": channel.id}),
            ("get_messages", {"channel_id": channel.id, "topic": "MCP"}),
            ("search", {"query": "Unique mcp regression"}),
            ("get_task_board", {}),
        ]:
            response = self.rpc(token, "tools/call", {"name": name, "arguments": args})
            self.assertFalse(
                response.json()["result"].get("isError", False), (name, response.content)
            )
            if name in {"get_messages", "search", "list_topics"}:
                self.assertIn("MCP", response.content.decode())
        board = json.loads(
            self.rpc(token, "tools/call", {"name": "get_task_board", "arguments": {}}).json()[
                "result"
            ]["content"][0]["text"]
        )
        created = self.rpc(
            token,
            "tools/call",
            {
                "name": "create_task",
                "arguments": {"title": "MCP task", "column_id": board["columns"][0]["id"]},
            },
        )
        self.assertFalse(created.json()["result"].get("isError", False), created.content)
        task_id = json.loads(created.json()["result"]["content"][0]["text"])["task_id"]
        updated = self.rpc(
            token,
            "tools/call",
            {"name": "update_task", "arguments": {"task_id": task_id, "title": "MCP task updated"}},
        )
        self.assertFalse(updated.json()["result"].get("isError", False), updated.content)
        fetched = self.rpc(
            token, "tools/call", {"name": "fetch", "arguments": {"id": f"task:{task_id}"}}
        )
        self.assertIn("MCP task updated", fetched.content.decode())

    def test_secrets_are_hashed_and_audit_has_no_content(self) -> None:
        from zerver.models.mcp_access import MCPAccessAudit, MCPAccessToken

        token = self.manual()
        self.assertNotEqual(MCPAccessToken.objects.get().digest, token)
        self.assertEqual(len(MCPAccessToken.objects.get().digest), 64)
        self.rpc(
            token,
            "tools/call",
            {"name": "search", "arguments": {"query": "sensitive search phrase"}},
        )
        events = list(MCPAccessAudit.objects.values("tool_name", "outcome"))
        self.assertNotIn("sensitive search phrase", json.dumps(events))
        self.assertIn({"tool_name": "search", "outcome": "success"}, events)

    def test_old_active_connection_stays_visible_after_revoked_history(self) -> None:
        from datetime import timedelta

        from django.utils.timezone import now

        from zerver.models.mcp_access import MCPAccessGrant

        self.manual()
        active = MCPAccessGrant.objects.get(user=self.user)
        MCPAccessGrant.objects.bulk_create(
            [
                MCPAccessGrant(
                    realm=self.user.realm,
                    user=self.user,
                    name=f"Revoked {index}",
                    scopes=["team:read"],
                    resource=self.resource,
                    expires_at=now() + timedelta(days=1),
                    revoked_at=now(),
                )
                for index in range(100)
            ]
        )
        rows = self.client_get("/json/mcp/access").json()["connections"]
        self.assertIn(str(active.id), [row["id"] for row in rows])

    def test_error_reports_mask_oauth_secrets_and_bearer_header(self) -> None:
        from django.test import RequestFactory

        from zerver.filters import ZulipExceptionReporterFilter

        request = RequestFactory().post(
            "/mcp/token",
            {
                "code": "synthetic-code",
                "code_verifier": "synthetic-verifier",
                "refresh_token": "synthetic-refresh",
                "token": "synthetic-token",
            },
            HTTP_AUTHORIZATION="Bearer synthetic-bearer",
        )
        reporter_filter = ZulipExceptionReporterFilter()
        post = reporter_filter.get_post_parameters(request)
        meta = reporter_filter.get_safe_request_meta(request)
        for field in ["code", "code_verifier", "refresh_token", "token"]:
            self.assertEqual(post[field], "**********")
        self.assertEqual(meta["HTTP_AUTHORIZATION"], "********************")
