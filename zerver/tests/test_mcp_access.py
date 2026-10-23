import base64
import hashlib
import json
import os
import struct
import zlib
from datetime import timedelta
from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qs, urlsplit

from django.db import connection
from django.http import HttpResponse
from django.test import Client
from django.utils.timezone import now
from typing_extensions import override

from zerver.lib.mcp_access import audit
from zerver.lib.mcp_pending import argument_digest
from zerver.lib.mcp_policy import ASK_FIRST, AUTO, OFF, set_policies
from zerver.lib.mcp_tools import MAX_BASE64_LENGTH, TOOLS
from zerver.lib.test_classes import ZulipTestCase
from zerver.lib.test_helpers import ratelimit_rule
from zerver.models import (
    AgentRealmSettings,
    MCPAccessAudit,
    MCPAccessGrant,
    MCPAccessToolPolicy,
    MCPPendingCall,
    Message,
    UserMessage,
)
from zerver.models.needs import NeedResolution, NeedSnooze
from zerver.models.streams import get_stream
from zerver.views.mcp_server import MAX_BODY_BYTES

if TYPE_CHECKING:
    from django.test.client import _MonkeyPatchedWSGIResponse as TestHttpResponse


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

    def call(self, token: str, name: str, arguments: dict[str, object]) -> dict[str, Any]:
        result = self.rpc(token, "tools/call", {"name": name, "arguments": arguments}).json()[
            "result"
        ]
        if result["isError"]:
            return {"isError": True}
        return json.loads(result["content"][0]["text"])

    def test_upload_file_returns_url_and_markdown(self) -> None:
        token = self.manual(write=True)
        png = base64.b64encode(b"\x89PNG\r\n\x1a\nchart").decode()
        output = self.call(
            token,
            "upload_file",
            {"filename": "chart.png", "content_base64": png, "content_type": "image/png"},
        )
        self.assertTrue(output["url"].startswith("/user_uploads/"))
        self.assertEqual(output["filename"], "chart.png")
        self.assertEqual(output["markdown"], f"[chart.png]({output['url']})")
        from zerver.models import Attachment

        attachment = Attachment.objects.get(path_id=output["url"].removeprefix("/user_uploads/"))
        self.assertEqual(attachment.owner_id, self.user.id)
        self.assertTrue(
            self.call(token, "upload_file", {"filename": "a", "content_base64": "!!"})["isError"]
        )
        self.assertTrue(
            self.call(self.manual(), "upload_file", {"filename": "a.txt", "content_base64": png})[
                "isError"
            ]
        )

    def chart_png(self, size: int) -> bytes:
        def chunk(kind: bytes, body: bytes) -> bytes:
            return (
                struct.pack(">I", len(body))
                + kind
                + body
                + struct.pack(">I", zlib.crc32(kind + body))
            )

        # Stored (uncompressed) rows of noise, so the file is about `size` bytes.
        width = int((size / 3) ** 0.5)
        rows = b"".join(b"\x00" + os.urandom(width * 3) for _ in range(width))
        return (
            b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", width, width, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(rows, 0))
            + chunk(b"IEND", b"")
        )

    def upload_error(self, token: str, arguments: dict[str, object]) -> str:
        response = self.rpc(token, "tools/call", {"name": "upload_file", "arguments": arguments})
        result = json.loads(response.content)["result"]
        self.assertTrue(result["isError"])
        return str(result["content"][0]["text"])

    def test_upload_file_accepts_report_charts(self) -> None:
        from zerver.models import Attachment

        token = self.manual(write=True)
        for size in (19_000, 1_000_000):
            png = self.chart_png(size)
            encoded = base64.b64encode(png).decode()
            wrapped = "\n".join(encoded[i : i + 76] for i in range(0, len(encoded), 76))
            for content in (
                encoded,
                f"data:image/png;base64,{encoded}",
                wrapped + "\n",
                encoded.rstrip("="),
            ):
                output = self.call(
                    token,
                    "upload_file",
                    {
                        "filename": "chart.png",
                        "content_base64": content,
                        "content_type": "image/png",
                    },
                )
                self.assertEqual(output["markdown"], f"[chart.png]({output['url']})")
                attachment = Attachment.objects.get(
                    path_id=str(output["url"]).removeprefix("/user_uploads/")
                )
                self.assertEqual(attachment.size, len(png))

    def test_upload_file_rejections_name_the_limit(self) -> None:
        token = self.manual(write=True)
        encoded = base64.b64encode(self.chart_png(19_000)).decode()
        png = {"filename": "chart.png", "content_type": "image/png"}

        self.assertIn(
            "not valid base64",
            self.upload_error(token, {**png, "content_base64": "!!"}),
        )
        self.assertIn(
            "not valid base64",
            self.upload_error(token, {**png, "content_base64": "A"}),
        )
        self.assertIn(
            "content_type",
            self.upload_error(token, {**png, "content_base64": encoded, "content_type": "png"}),
        )
        self.assertIn(
            "at most 100 characters",
            self.upload_error(token, {**png, "content_base64": encoded, "filename": "a" * 101}),
        )
        with self.settings(MAX_FILE_UPLOAD_SIZE=1):
            big = base64.b64encode(os.urandom(1024 * 1024 + 1)).decode()
            self.assertIn(
                "upload limit of 1 MiB",
                self.upload_error(token, {**png, "content_base64": big}),
            )
        self.assertIn(
            f"at most {MAX_BASE64_LENGTH} characters",
            self.upload_error(token, {**png, "content_base64": "A" * (MAX_BASE64_LENGTH + 1)}),
        )

    def test_mcp_body_over_the_limit_names_it(self) -> None:
        token = self.manual(write=True)
        response = self.client_post(
            "/mcp",
            json.dumps(
                {"jsonrpc": "2.0", "id": 1, "method": "ping", "params": {"x": "A" * MAX_BODY_BYTES}}
            ),
            content_type="application/json",
            HTTP_AUTHORIZATION="Bearer " + token,
        )
        self.assertEqual(response.status_code, 413)
        self.assertIn("exceeds the 21 MB limit", response.json()["error"]["message"])

    def test_profile_reactions_edit_and_direct_messages(self) -> None:
        from zerver.models import Message, Reaction

        token = self.manual(write=True)
        me = self.call(token, "get_me", {})
        self.assertEqual(me["user_id"], self.user.id)
        self.assertNotIn("email", me)
        other = self.example_user("othello")
        sent = self.call(
            token, "send_direct_message", {"recipient_user_ids": [other.id], "content": "one"}
        )
        message_id = sent["id"]
        self.call(token, "add_reaction", {"message_id": message_id, "emoji_name": "smile"})
        self.assertTrue(
            Reaction.objects.filter(message_id=message_id, user_profile=self.user).exists()
        )
        self.call(token, "remove_reaction", {"message_id": message_id, "emoji_name": "smile"})
        self.assertFalse(Reaction.objects.filter(message_id=message_id).exists())
        self.call(token, "edit_message", {"message_id": message_id, "content": "two"})
        self.assertEqual(Message.objects.get(id=message_id).content, "two")
        history = self.call(token, "get_direct_messages", {"user_ids": [other.id]})
        self.assertEqual(history["messages"][-1]["content"], "two")
        self.assertTrue(
            self.call(self.manual(), "edit_message", {"message_id": message_id, "content": "x"})[
                "isError"
            ]
        )

    def test_discovery_and_bearer_challenge(self) -> None:
        metadata = self.client_get("/.well-known/oauth-protected-resource/mcp")
        self.assertEqual(metadata.status_code, 200)
        self.assertEqual(metadata.json()["resource"], self.resource)
        self.assertEqual(metadata.json()["resource_name"], "sanji.space")
        auth = self.client_get("/.well-known/oauth-authorization-server").json()
        self.assertIn("S256", auth["code_challenge_methods_supported"])
        response = self.client_post("/mcp", "{}", content_type="application/json")
        self.assertEqual(response.status_code, 401)
        self.assertIn("resource_metadata", response["WWW-Authenticate"])

    def test_server_brand_metadata(self) -> None:
        response = self.rpc(self.manual(), "initialize", {"protocolVersion": "2025-11-25"})
        info = response.json()["result"]["serverInfo"]
        self.assertEqual(info["name"], "sanji.space")
        self.assertEqual(info["title"], "sanji.space")
        self.assertEqual(info["websiteUrl"], "https://sanji.space")
        icon = info["icons"][0]
        self.assertEqual(icon["mimeType"], "image/png")
        self.assertEqual(icon["sizes"], ["512x512"])
        self.assertEqual(urlsplit(icon["src"]).netloc, urlsplit(self.resource).netloc)
        self.assertIn("zulip-icon-512x512", icon["src"])

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

    def error_and_reason(
        self, token: str, name: str, arguments: dict[str, object]
    ) -> tuple[str, object]:
        result = json.loads(
            self.rpc(token, "tools/call", {"name": name, "arguments": arguments}).content
        )["result"]
        self.assertTrue(result["isError"])
        entry = MCPAccessAudit.objects.filter(tool_name=name).latest("id")
        self.assertEqual(entry.outcome, "rejected")
        return result["content"][0]["text"], entry.detail.get("reason")

    def test_rejected_calls_say_why_without_revealing_what_exists(self) -> None:
        token = self.manual()
        text, reason = self.error_and_reason(token, "fetch", {"id": "message:abc"})
        self.assertIn("id: String should match pattern", text)
        self.assertEqual(reason, "validation")
        _text, reason = self.error_and_reason(token, "fetch", {"id": "123"})
        self.assertEqual(reason, "validation")

        unknown_text, reason = self.error_and_reason(token, "fetch", {"id": "message:999999999"})
        self.assertEqual(unknown_text, "Invalid message(s)")
        self.assertEqual(reason, "not_accessible")

        self.make_stream("secret-mcp", invite_only=True)
        self.subscribe(self.example_user("othello"), "secret-mcp")
        secret_id = self.send_stream_message(
            self.example_user("othello"), "secret-mcp", "Secret MCP words"
        )
        private_text, reason = self.error_and_reason(token, "fetch", {"id": f"message:{secret_id}"})
        self.assertEqual(private_text, unknown_text)
        self.assertEqual(reason, "not_accessible")

        unknown_task, reason = self.error_and_reason(token, "fetch", {"id": "task:999999999"})
        self.assertEqual(unknown_task, "Task does not exist.")
        self.assertEqual(reason, "not_accessible")

        text, reason = self.error_and_reason(
            token, "add_reaction", {"message_id": 1, "emoji_name": "tada"}
        )
        self.assertEqual(text, "This connection has read access only.")
        self.assertEqual(reason, "read_only")

        write_token = self.manual(write=True)
        text, reason = self.error_and_reason(
            write_token, "edit_message", {"message_id": secret_id, "content": "x"}
        )
        self.assertEqual(text, "Invalid message(s)")
        self.assertEqual(reason, "api_error")

        audit_dump = json.dumps(list(MCPAccessAudit.objects.values()), default=str)
        self.assertNotIn("Secret MCP words", audit_dump)

    def test_successful_calls_record_no_reason(self) -> None:
        token = self.manual()
        self.assertNotIn("isError", self.call(token, "get_me", {}))
        entry = MCPAccessAudit.objects.filter(tool_name="get_me").latest("id")
        self.assertEqual((entry.outcome, entry.detail), ("success", {}))

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

    def test_update_task_lets_a_member_move_but_not_edit_a_card_they_see(self) -> None:
        from zerver.actions.tasks import do_create_task
        from zerver.lib.tasks import get_or_create_default_board
        from zerver.models import TaskHistory

        board = get_or_create_default_board(self.user.realm)
        columns = list(board.columns.order_by("order"))
        card = do_create_task(
            user_profile=self.example_user("iago"),
            board=board,
            column=columns[0],
            title="Iago card",
        )
        token = self.manual(write=True)

        moved = self.call(token, "update_task", {"task_id": card.id, "column_id": columns[1].id})
        self.assertNotIn("isError", moved)
        card.refresh_from_db()
        self.assertEqual(card.column_id, columns[1].id)
        self.assertEqual(
            TaskHistory.objects.get(task=card, kind=TaskHistory.MOVED).acting_user_id, self.user.id
        )

        edited = self.call(
            token,
            "update_task",
            {"task_id": card.id, "title": "Taken over", "column_id": columns[2].id},
        )
        self.assertEqual(edited, {"isError": True})
        card.refresh_from_db()
        self.assertEqual(card.title, "Iago card")
        self.assertEqual(card.column_id, columns[1].id)

    def test_create_task_without_a_due_date_is_accepted(self) -> None:
        from zerver.lib.tasks import get_or_create_default_board
        from zerver.models import Task

        board = get_or_create_default_board(self.user.realm)
        column_id = board.columns.order_by("order")[0].id
        token = self.manual(write=True)

        created = self.call(token, "create_task", {"title": "No deadline", "column_id": column_id})

        self.assertNotIn("isError", created)
        self.assertIsNone(Task.objects.get(title="No deadline").due_at)

    def test_task_tools_take_priority_labels_and_due_date(self) -> None:
        from zerver.lib.tasks import get_or_create_default_board
        from zerver.models import Task

        board = get_or_create_default_board(self.user.realm)
        column_id = board.columns.order_by("order")[0].id
        token = self.manual(write=True)

        created = self.call(
            token,
            "create_task",
            {
                "title": "Planned",
                "column_id": column_id,
                "priority": 3,
                "labels": ["ops", "q4"],
                "due_at": 1893456000,
            },
        )
        self.assertNotIn("isError", created)
        card = Task.objects.get(title="Planned")
        self.assertEqual(card.priority, 3)
        self.assertEqual(card.labels, ["ops", "q4"])
        assert card.due_at is not None
        self.assertEqual(int(card.due_at.timestamp()), 1893456000)

        updated = self.call(
            token,
            "update_task",
            {"task_id": card.id, "priority": 1, "labels": ["ops"], "clear_due_at": True},
        )
        self.assertNotIn("isError", updated)
        card.refresh_from_db()
        self.assertEqual(card.priority, 1)
        self.assertEqual(card.labels, ["ops"])
        self.assertIsNone(card.due_at)

        bad_arguments: list[dict[str, object]] = [
            {"priority": 4},
            {"labels": ["x" * 41]},
            {"labels": ["a"] * 11},
        ]
        for bad in bad_arguments:
            rejected = self.call(token, "update_task", {"task_id": card.id, **bad})
            self.assertEqual(rejected, {"isError": True})
        card.refresh_from_db()
        self.assertEqual(card.priority, 1)

    def test_update_task_priority_follows_the_edit_permission(self) -> None:
        from zerver.actions.tasks import do_create_task
        from zerver.lib.tasks import get_or_create_default_board
        from zerver.models import Task

        board = get_or_create_default_board(self.user.realm)
        card = do_create_task(
            user_profile=self.example_user("iago"),
            board=board,
            column=board.columns.order_by("order")[0],
            title="Iago card",
        )
        token = self.manual(write=True)
        edited = self.call(token, "update_task", {"task_id": card.id, "priority": 3})
        self.assertEqual(edited, {"isError": True})
        self.assertEqual(Task.objects.get(id=card.id).priority, 0)

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

    def enforce(self) -> None:
        AgentRealmSettings.objects.update_or_create(
            realm=self.user.realm, defaults={"mcp_policy_enforced": True}
        )

    def latest_grant(self) -> MCPAccessGrant:
        return MCPAccessGrant.objects.latest("created_at")

    def tool_names(self, token: str) -> set[str]:
        return {
            tool["name"]
            for tool in json.loads(self.rpc(token, "tools/list").content)["result"]["tools"]
        }

    def rpc_result(self, token: str, name: str, arguments: dict[str, object]) -> dict[str, Any]:
        return json.loads(
            self.rpc(token, "tools/call", {"name": name, "arguments": arguments}).content
        )["result"]

    def test_policies_do_nothing_while_enforcement_is_off(self) -> None:
        token = self.manual(write=True)
        set_policies(self.latest_grant(), self.user, {"send_message": OFF, "get_me": OFF})
        self.assertEqual(self.tool_names(token), set(TOOLS))
        self.assertFalse(self.rpc_result(token, "get_me", {})["isError"])
        self.subscribe(self.user, "Denmark")
        args = {
            "channel_id": get_stream("Denmark", self.user.realm).id,
            "topic": "t",
            "content": "Sent while the flag is off.",
        }
        self.assertFalse(self.rpc_result(token, "send_message", args)["isError"])
        self.assertFalse(MCPPendingCall.objects.exists())

    def test_off_tools_are_hidden_and_look_like_unknown_tools(self) -> None:
        # AC-V1-004
        self.enforce()
        token = self.manual(write=True)
        set_policies(self.latest_grant(), self.user, {"get_me": OFF, "delete_task": OFF})
        names = self.tool_names(token)
        self.assertNotIn("get_me", names)
        self.assertNotIn("delete_task", names)
        # Ask first stays visible: the client may ask.
        self.assertIn("send_message", names)
        unknown = self.rpc(token, "tools/call", {"name": "no_such_tool", "arguments": {}})
        off = self.rpc(token, "tools/call", {"name": "get_me", "arguments": {}})
        self.assertEqual(off.status_code, unknown.status_code)
        self.assertEqual(off.content, unknown.content)
        self.assertEqual(
            list(
                MCPAccessAudit.objects.filter(outcome="blocked").values_list("tool_name", flat=True)
            ),
            ["get_me"],
        )

    def test_tools_list_advertises_the_corrected_hints(self) -> None:
        # AC-V1-005
        token = self.manual(write=True)
        tools = {
            tool["name"]: tool["annotations"]
            for tool in json.loads(self.rpc(token, "tools/list").content)["result"]["tools"]
        }
        for name, annotations in tools.items():
            self.assertEqual(
                annotations["destructiveHint"], name in {"delete_task", "edit_message"}
            )
        self.assertFalse(tools["send_message"]["idempotentHint"])
        self.assertTrue(tools["add_reaction"]["idempotentHint"])
        self.assertTrue(tools["get_me"]["idempotentHint"])

    def test_write_calls_are_rate_limited_per_grant(self) -> None:
        # AC-V1-006
        self.enforce()
        token = self.manual(write=True)
        self.subscribe(self.user, "Denmark")
        message_id = self.send_stream_message(self.user, "Denmark")
        with ratelimit_rule(60, 2, domain="mcp_write_by_grant"):
            for emoji in ("tada", "smile"):
                result = self.rpc_result(
                    token, "add_reaction", {"message_id": message_id, "emoji_name": emoji}
                )
                self.assertFalse(result["isError"])
            limited = self.rpc_result(
                token, "add_reaction", {"message_id": message_id, "emoji_name": "heart"}
            )
            self.assertTrue(limited["isError"])
            self.assertIn("Too many write calls", limited["content"][0]["text"])
            # Reads are not counted, and another grant has its own allowance.
            self.assertFalse(self.rpc_result(token, "get_me", {})["isError"])
            other = self.manual(write=True)
            self.assertFalse(
                self.rpc_result(
                    other, "add_reaction", {"message_id": message_id, "emoji_name": "heart"}
                )["isError"]
            )
        self.assertEqual(MCPAccessAudit.objects.filter(outcome="rate_limited").count(), 1)

    def test_a_legacy_grant_is_not_rate_limited_or_asked(self) -> None:
        # AC-V1-002 end to end
        self.enforce()
        token = self.manual(write=True)
        MCPAccessGrant.objects.filter(id=self.latest_grant().id).update(policy_mode="legacy")
        self.subscribe(self.user, "Denmark")
        args = {
            "channel_id": get_stream("Denmark", self.user.realm).id,
            "topic": "t",
            "content": "Legacy grants send at once.",
        }
        with ratelimit_rule(60, 1, domain="mcp_write_by_grant"):
            for _index in range(3):
                self.assertFalse(self.rpc_result(token, "send_message", args)["isError"])
        self.assertEqual(self.tool_names(token), set(TOOLS))
        self.assertFalse(MCPPendingCall.objects.exists())

    def test_auto_calls_are_audited_with_a_digest_and_no_content(self) -> None:
        # AC-V1-007
        self.enforce()
        token = self.manual(write=True)
        self.subscribe(self.user, "Denmark")
        message_id = self.send_stream_message(self.user, "Denmark", "private words")
        args = {"message_id": message_id, "emoji_name": "tada"}
        self.assertFalse(self.rpc_result(token, "add_reaction", args)["isError"])
        self.assertTrue(self.rpc_result(token, "add_reaction", args)["isError"])
        entries = list(
            MCPAccessAudit.objects.filter(tool_name="add_reaction")
            .order_by("id")
            .values("outcome", "argument_digest")
        )
        digest = argument_digest("add_reaction", args)
        self.assertEqual(
            entries,
            [
                {"outcome": "success", "argument_digest": digest},
                {"outcome": "rejected", "argument_digest": digest},
            ],
        )
        self.assertNotIn(
            "private words", json.dumps(list(MCPAccessAudit.objects.values()), default=str)
        )

    def test_a_read_only_grant_still_cannot_call_write_tools(self) -> None:
        self.enforce()
        token = self.manual()
        result = self.rpc_result(token, "add_reaction", {"message_id": 1, "emoji_name": "tada"})
        self.assertTrue(result["isError"])
        self.assertEqual(
            list(
                MCPAccessAudit.objects.filter(tool_name="add_reaction").values_list(
                    "outcome", flat=True
                )
            ),
            ["rejected"],
        )

    def test_an_ask_first_call_waits_for_the_owner_and_then_returns_the_receipt(self) -> None:
        # AC-V1-008, AC-V1-009, AC-V1-011 through the MCP endpoint
        self.enforce()
        token = self.manual(write=True)
        set_policies(self.latest_grant(), self.user, {"send_message": ASK_FIRST})
        self.subscribe(self.user, "Denmark")
        args = {
            "channel_id": get_stream("Denmark", self.user.realm).id,
            "topic": "Release",
            "content": "Please hold this until approved.",
        }
        first = self.rpc_result(token, "send_message", args)
        self.assertTrue(first["isError"])
        self.assertIn("waiting", first["content"][0]["text"])
        self.assertEqual(self.rpc_result(token, "send_message", args), first)
        self.assertEqual(MCPPendingCall.objects.count(), 1)
        self.assertFalse(
            Message.objects.filter(realm_id=self.user.realm_id, content=args["content"]).exists()
        )
        call = MCPPendingCall.objects.get()
        result = self.client_post(
            f"/json/mcp/calls/{call.id}/decision",
            {
                "decision": "approve",
                "argument_digest": call.argument_digest,
                "nonce": str(call.nonce),
            },
        )
        self.assert_json_success(result)
        receipt = self.rpc_result(token, "send_message", args)
        self.assertFalse(receipt["isError"])
        message = Message.objects.get(content=args["content"])
        self.assertEqual(json.loads(receipt["content"][0]["text"])["id"], message.id)
        self.assertEqual(MCPPendingCall.objects.count(), 1)
        outcomes = list(
            MCPAccessAudit.objects.filter(tool_name="send_message")
            .order_by("id")
            .values_list("outcome", flat=True)
        )
        self.assertEqual(outcomes, ["asked", "approved", "executed"])

    def test_a_new_grant_sends_a_direct_message_without_asking(self) -> None:
        self.enforce()
        token = self.manual(write=True)
        other = self.example_user("othello")
        result = self.rpc_result(
            token,
            "send_direct_message",
            {"recipient_user_ids": [other.id], "content": "No approval needed."},
        )
        self.assertFalse(result["isError"])
        self.assertTrue(Message.objects.filter(content="No approval needed.").exists())
        self.assertFalse(MCPPendingCall.objects.exists())
        outcomes = list(
            MCPAccessAudit.objects.filter(tool_name="send_direct_message").values_list(
                "outcome", flat=True
            )
        )
        self.assertEqual(outcomes, ["success"])

    def test_an_ask_first_call_with_an_unreachable_target_is_rejected_not_queued(self) -> None:
        self.enforce()
        token = self.manual(write=True)
        stream = self.make_stream("hidden-room", invite_only=True)
        result = self.rpc_result(
            token, "send_message", {"channel_id": stream.id, "topic": "t", "content": "hi"}
        )
        self.assertTrue(result["isError"])
        self.assertFalse(MCPPendingCall.objects.exists())

    def put_tools(self, grant: MCPAccessGrant, policies: dict[str, str]) -> "TestHttpResponse":
        return self.client_put(
            f"/json/mcp/access/{grant.id}/tools", {"policies": json.dumps(policies)}
        )

    def test_tools_endpoint_lists_the_tools_a_connection_can_use(self) -> None:
        self.manual(write=True)
        grant = self.latest_grant()
        MCPAccessGrant.objects.filter(id=grant.id).update(policy_mode="legacy")
        result = self.assert_json_success(self.client_get(f"/json/mcp/access/{grant.id}/tools"))
        self.assertEqual(result["grant"]["policy_mode"], "legacy")
        self.assertFalse(result["enforced"])
        tools = {tool["name"]: tool for tool in result["tools"]}
        self.assertEqual(set(tools), set(TOOLS))
        self.assertEqual({tool["policy"] for tool in tools.values()}, {"allow"})
        self.assertEqual(tools["get_me"]["kind"], "read")
        self.assertEqual(tools["get_me"]["default_policy"], "allow")
        self.assertEqual(tools["send_message"]["default_policy"], "allow")
        self.assertEqual(tools["add_reaction"]["default_policy"], "allow")
        self.assertEqual(tools["send_message"]["kind"], "write")
        self.assertEqual(tools["delete_task"]["kind"], "destructive")
        self.assertNotIn("**", tools["send_message"]["description"])
        self.manual()
        read_only = self.latest_grant()
        result = self.assert_json_success(self.client_get(f"/json/mcp/access/{read_only.id}/tools"))
        self.assertEqual(result["grant"]["policy_mode"], "managed")
        names = {tool["name"] for tool in result["tools"]}
        self.assertNotIn("send_message", names)
        self.assertIn("get_me", names)
        defaults = {tool["name"]: tool["policy"] for tool in result["tools"]}
        self.assertEqual(defaults["get_me"], "allow")

    def test_changing_a_policy_is_saved_audited_and_shown(self) -> None:
        # AC-V1-013
        self.manual(write=True)
        grant = self.latest_grant()
        result = self.assert_json_success(
            self.put_tools(grant, {"send_message": "block", "delete_task": "allow"})
        )
        policies = {tool["name"]: tool["policy"] for tool in result["tools"]}
        self.assertEqual(policies["send_message"], "block")
        self.assertEqual(policies["delete_task"], "allow")
        self.assertEqual(policies["edit_message"], "allow")
        self.assertEqual(policies["add_reaction"], "allow")
        stored = {
            row.tool_name: row.policy for row in MCPAccessToolPolicy.objects.filter(grant=grant)
        }
        self.assertEqual(stored["send_message"], "block")
        entries = list(
            MCPAccessAudit.objects.filter(outcome="policy_changed")
            .order_by("id")
            .values("tool_name", "actor_id", "detail")
        )
        self.assertEqual(
            entries,
            [
                {
                    "tool_name": "send_message",
                    "actor_id": self.user.id,
                    "detail": {"policy": "block", "was": "allow"},
                },
            ],
        )
        # Saving the same thing again changes nothing and adds no entry.
        self.assert_json_success(self.put_tools(grant, {"send_message": "block"}))
        self.assertEqual(MCPAccessAudit.objects.filter(outcome="policy_changed").count(), 1)

    def test_changing_a_legacy_grant_keeps_its_other_tools_on_auto(self) -> None:
        self.manual(write=True)
        grant = self.latest_grant()
        MCPAccessGrant.objects.filter(id=grant.id).update(policy_mode="legacy")
        result = self.assert_json_success(self.put_tools(grant, {"send_message": "approve"}))
        self.assertEqual(result["grant"]["policy_mode"], "managed")
        policies = {tool["name"]: tool["policy"] for tool in result["tools"]}
        self.assertEqual(
            {name for name, policy in policies.items() if policy != "allow"}, {"send_message"}
        )

    def test_policy_changes_are_rejected_when_invalid_or_not_the_owners(self) -> None:
        self.manual(write=True)
        grant = self.latest_grant()
        result = self.put_tools(grant, {"send_message": "maybe"})
        self.assertEqual(result.status_code, 400)
        result = self.put_tools(grant, {"no_such_tool": "block"})
        self.assert_json_error(result, "Unknown tool.")
        result = self.put_tools(grant, {"get_me": "approve"})
        self.assert_json_error(result, "A read tool can be Auto or Off, not Ask first.")
        self.assertFalse(MCPAccessToolPolicy.objects.exists())
        for email in ("iago", "othello"):
            self.login(email)
            self.assertEqual(self.put_tools(grant, {"get_me": "block"}).status_code, 404)
            self.assertEqual(self.client_get(f"/json/mcp/access/{grant.id}/tools").status_code, 404)
        self.login_user(self.user)
        MCPAccessGrant.objects.filter(id=grant.id).update(revoked_at=now())
        self.assertEqual(self.put_tools(grant, {"get_me": "block"}).status_code, 404)
        self.assertFalse(MCPAccessToolPolicy.objects.exists())

    def test_audit_feed_shows_members_their_own_and_admins_all(self) -> None:
        # AC-V1-020
        self.manual(write=True)
        mine = self.latest_grant()
        other_user = self.example_user("othello")
        theirs = MCPAccessGrant.objects.create(
            realm=other_user.realm,
            user=other_user,
            name="Their agent",
            scopes=["team:read"],
            resource="http://zulip.testserver/mcp",
            expires_at=now() + timedelta(days=1),
        )
        audit(theirs, "get_me", "success")
        audit(mine, "send_message", "asked", argument_digest="a" * 64, actor=self.user)
        result = self.assert_json_success(self.client_get("/json/mcp/audit"))
        self.assertFalse(result["has_more"])
        entries = result["entries"]
        self.assertEqual({entry["connection"] for entry in entries}, {"Hermes"})
        self.assertEqual({entry["owner"] for entry in entries}, {self.user.full_name})
        asked = next(entry for entry in entries if entry["outcome"] == "asked")
        self.assertEqual(asked["digest"], "a" * 12)
        self.assertEqual(asked["actor"], self.user.full_name)
        self.assertEqual(
            set(asked),
            {
                "id",
                "created_at",
                "connection",
                "owner",
                "tool",
                "outcome",
                "digest",
                "actor",
                "policy",
            },
        )
        self.assertNotIn(str(mine.id), json.dumps(result))
        self.login("iago")
        everything = self.assert_json_success(self.client_get("/json/mcp/audit"))["entries"]
        self.assertEqual({entry["connection"] for entry in everything}, {"Hermes", "Their agent"})
        self.login("othello")
        own = self.assert_json_success(self.client_get("/json/mcp/audit"))["entries"]
        self.assertEqual({entry["connection"] for entry in own}, {"Their agent"})

    def test_audit_feed_pages_newest_first(self) -> None:
        self.manual(write=True)
        grant = self.latest_grant()
        for index in range(5):
            audit(grant, "get_me", f"outcome{index}")
        page = self.assert_json_success(self.client_get("/json/mcp/audit", {"limit": "2"}))
        self.assertEqual([entry["outcome"] for entry in page["entries"]], ["outcome4", "outcome3"])
        self.assertTrue(page["has_more"])
        older = self.assert_json_success(
            self.client_get(
                "/json/mcp/audit", {"limit": "2", "before": str(page["entries"][-1]["id"])}
            )
        )
        self.assertEqual([entry["outcome"] for entry in older["entries"]], ["outcome2", "outcome1"])
        self.assertEqual(self.client_get("/json/mcp/audit", {"limit": "0"}).status_code, 400)

    def mention_of_user(self, text: str = "please look") -> int:
        self.subscribe(self.user, "Denmark")
        sender = self.example_user("othello")
        self.subscribe(sender, "Denmark")
        return self.send_stream_message(
            sender, "Denmark", f"@**{self.user.full_name}** {text}", topic_name="needs"
        )

    def test_get_needs_lists_a_mention_without_approval_values(self) -> None:
        token = self.manual()
        message_id = self.mention_of_user()

        output = self.call(token, "get_needs", {})

        [item] = output["items"]
        self.assertEqual(item["id"], f"mention:{message_id}")
        self.assertEqual(item["kind"], "mention")
        self.assertEqual(item["from"], self.example_user("othello").full_name)
        self.assertEqual(item["channel_id"], get_stream("Denmark", self.user.realm).id)
        self.assertEqual(item["topic"], "needs")
        self.assertEqual(item["text"], f"@{self.user.full_name} please look")
        self.assertEqual(item["url"], self.user.realm.url + f"/#narrow/id/{message_id}")
        self.assertTrue(item["can_mark_done"])
        self.assertFalse(item["team_wide"])
        self.assertEqual(output["counts"]["mention"], 1)
        self.assertFalse(output["has_more"])
        for hidden in ("nonce", "operation_hash", "approval_version", "job_version", "mcp_call"):
            self.assertNotIn(hidden, item)
            self.assertNotIn(hidden, json.dumps(output))

    def test_get_needs_limit_and_kind_filter(self) -> None:
        token = self.manual()
        for index in range(3):
            self.mention_of_user(f"ping {index}")
        output = self.call(token, "get_needs", {"limit": 2})
        self.assertEqual(len(output["items"]), 2)
        self.assertTrue(output["has_more"])
        self.assertEqual(self.call(token, "get_needs", {"kind": "approval"})["items"], [])
        self.assertTrue(self.call(token, "get_needs", {"kind": "other"})["isError"])

    def test_resolve_and_reopen_needs(self) -> None:
        token = self.manual(write=True)
        need_id = f"mention:{self.mention_of_user()}"

        self.assertEqual(
            self.call(token, "resolve_needs", {"need_ids": [need_id]}),
            {"done": [need_id], "failed": []},
        )
        self.assertEqual(self.call(token, "get_needs", {})["items"], [])
        [resolved] = self.call(token, "get_needs", {"status": "resolved"})["items"]
        self.assertEqual(resolved["id"], need_id)
        self.assertFalse(resolved["can_mark_done"])
        self.assertIsNotNone(resolved["resolved_at"])

        self.assertEqual(
            self.call(token, "reopen_needs", {"need_ids": [need_id]}),
            {"done": [need_id], "failed": []},
        )
        self.assertEqual(
            [item["id"] for item in self.call(token, "get_needs", {})["items"]], [need_id]
        )

    def test_resolve_needs_reports_the_items_that_fail(self) -> None:
        token = self.manual(write=True)
        output = self.call(token, "resolve_needs", {"need_ids": ["mention:999999"]})
        self.assertEqual(output, {"done": [], "failed": ["mention:999999"]})

    def test_snooze_and_unsnooze_needs(self) -> None:
        token = self.manual(write=True)
        need_id = f"mention:{self.mention_of_user()}"

        output = self.call(token, "snooze_needs", {"need_ids": [need_id]})

        self.assertIn("until", output)
        self.assertEqual(self.call(token, "get_needs", {})["items"], [])
        self.assertEqual(
            self.call(token, "unsnooze_needs", {"need_ids": [need_id]}),
            {"unsnoozed": [need_id]},
        )
        self.assertEqual(
            [item["id"] for item in self.call(token, "get_needs", {})["items"]], [need_id]
        )

    def test_need_tools_leave_approvals_and_questions_to_the_app(self) -> None:
        token = self.manual(write=True)
        mention_id = f"mention:{self.mention_of_user()}"
        for name in ("resolve_needs", "reopen_needs", "snooze_needs", "unsnooze_needs"):
            for other in ("approval:abc", "mcp_call:abc", "decision:abc"):
                self.assertTrue(self.call(token, name, {"need_ids": [other]})["isError"])
                # One item of another kind fails the whole call.
                self.assertTrue(
                    self.call(token, name, {"need_ids": [mention_id, other]})["isError"]
                )
        self.assertFalse(NeedSnooze.objects.exists())
        self.assertFalse(NeedResolution.objects.exists())
        self.assertEqual(len(self.call(token, "get_needs", {})["items"]), 1)

    def test_an_ask_first_need_tool_holds_no_call_for_another_kind(self) -> None:
        self.enforce()
        token = self.manual(write=True)
        set_policies(self.latest_grant(), self.user, {"snooze_needs": ASK_FIRST})
        self.assertTrue(self.call(token, "snooze_needs", {"need_ids": ["mcp_call:abc"]})["isError"])
        self.assertFalse(MCPPendingCall.objects.exists())

    def test_a_read_only_connection_reads_needs_but_cannot_change_them(self) -> None:
        token = self.manual()
        need_id = f"mention:{self.mention_of_user()}"
        self.assertEqual(len(self.call(token, "get_needs", {})["items"]), 1)
        for name in ("resolve_needs", "reopen_needs", "snooze_needs", "unsnooze_needs"):
            self.assertTrue(self.call(token, name, {"need_ids": [need_id]})["isError"])
        self.assertEqual(len(self.call(token, "get_needs", {})["items"]), 1)

    def test_need_ids_must_have_the_shape_of_an_id(self) -> None:
        token = self.manual(write=True)
        for bad in ([], ["mention"], ["has space:1"], ["x" * 90 + ":1"], ["mention:1"] * 201):
            self.assertTrue(self.call(token, "resolve_needs", {"need_ids": bad})["isError"])

    def test_team_wide_items_need_include_team_wide(self) -> None:
        token = self.manual()
        sender = self.example_user("othello")
        others = [self.example_user("cordelia"), self.example_user("iago")]
        for member in [self.user, sender, *others]:
            self.subscribe(member, "Denmark")
        mentions = " ".join(f"@**{member.full_name}**" for member in [self.user, *others])
        message_id = self.send_stream_message(
            sender, "Denmark", f"{mentions} daily summary", topic_name="summary"
        )

        hidden = self.call(token, "get_needs", {})
        shown = self.call(token, "get_needs", {"include_team_wide": True})

        self.assertEqual(hidden["items"], [])
        self.assertEqual(hidden["team_wide_counts"]["mention"], 1)
        [item] = shown["items"]
        self.assertEqual(item["id"], f"mention:{message_id}")
        self.assertTrue(item["team_wide"])

    def is_read(self, message_id: int) -> bool:
        return UserMessage.objects.get(
            user_profile=self.user, message_id=message_id
        ).flags.read.is_set

    def unread_channel_message(self, topic: str = "reading") -> int:
        sender = self.example_user("othello")
        self.subscribe(self.user, "Denmark")
        self.subscribe(sender, "Denmark")
        return self.send_stream_message(sender, "Denmark", "Unread text.", topic_name=topic)

    def test_get_messages_marks_the_returned_messages_as_read(self) -> None:
        token = self.manual(write=True)
        message_id = self.unread_channel_message()
        channel_id = get_stream("Denmark", self.user.realm).id
        self.assertFalse(self.is_read(message_id))

        output = self.call(token, "get_messages", {"channel_id": channel_id, "topic": "reading"})

        self.assertEqual(output["marked_read_count"], 1)
        self.assertTrue(self.is_read(message_id))
        again = self.call(token, "get_messages", {"channel_id": channel_id, "topic": "reading"})
        self.assertEqual(again["marked_read_count"], 0)

    def test_get_messages_keeps_messages_unread_when_mark_read_is_false(self) -> None:
        token = self.manual(write=True)
        message_id = self.unread_channel_message()
        channel_id = get_stream("Denmark", self.user.realm).id

        output = self.call(
            token,
            "get_messages",
            {"channel_id": channel_id, "topic": "reading", "mark_read": False},
        )

        self.assertEqual(output["marked_read_count"], 0)
        self.assertFalse(self.is_read(message_id))

    def test_get_direct_messages_marks_the_conversation_read(self) -> None:
        token = self.manual(write=True)
        sender = self.example_user("othello")
        message_id = self.send_personal_message(sender, self.user, "Hello")
        self.assertFalse(self.is_read(message_id))

        kept = self.call(
            token, "get_direct_messages", {"user_ids": [sender.id], "mark_read": False}
        )
        self.assertEqual(kept["marked_read_count"], 0)
        self.assertFalse(self.is_read(message_id))

        output = self.call(token, "get_direct_messages", {"user_ids": [sender.id]})

        self.assertEqual(output["marked_read_count"], 1)
        self.assertTrue(self.is_read(message_id))

    def test_fetch_marks_a_message_read_but_search_does_not(self) -> None:
        token = self.manual(write=True)
        first = self.unread_channel_message("fetching")
        second = self.unread_channel_message("fetching")

        self.call(token, "search", {"query": "Unread"})
        self.assertFalse(self.is_read(first))
        self.assertFalse(self.is_read(second))

        kept = self.call(token, "fetch", {"id": f"message:{first}", "mark_read": False})
        self.assertEqual(kept["metadata"]["marked_read_count"], 0)
        self.assertFalse(self.is_read(first))

        output = self.call(token, "fetch", {"id": f"message:{first}"})
        self.assertEqual(output["metadata"]["marked_read_count"], 1)
        self.assertTrue(self.is_read(first))
        self.assertFalse(self.is_read(second))

    def test_mark_read_for_a_channel_a_topic_and_a_conversation(self) -> None:
        token = self.manual(write=True)
        channel_id = get_stream("Denmark", self.user.realm).id
        sender = self.example_user("othello")
        topic_a = self.unread_channel_message("topic a")
        topic_b = self.unread_channel_message("topic b")

        self.assertEqual(
            self.call(token, "mark_read", {"channel_id": channel_id, "topic": "topic a"}),
            {"marked_read": True},
        )
        self.assertTrue(self.is_read(topic_a))
        self.assertFalse(self.is_read(topic_b))

        self.assertEqual(
            self.call(token, "mark_read", {"channel_id": channel_id}), {"marked_read": True}
        )
        self.assertTrue(self.is_read(topic_b))

        direct = self.send_personal_message(sender, self.user, "Hello")
        other_direct = self.send_personal_message(self.example_user("cordelia"), self.user, "Hi")
        self.assertEqual(
            self.call(token, "mark_read", {"user_ids": [sender.id]}),
            {"marked_read": True, "more_unread": False},
        )
        self.assertTrue(self.is_read(direct))
        self.assertFalse(self.is_read(other_direct))

    def test_mark_read_needs_exactly_one_target(self) -> None:
        token = self.manual(write=True)
        channel_id = get_stream("Denmark", self.user.realm).id
        othello_id = self.example_user("othello").id
        bad_arguments: list[dict[str, object]] = [
            {},
            {"topic": "t"},
            {"channel_id": channel_id, "user_ids": [othello_id]},
            {"user_ids": [othello_id], "topic": "t"},
            {"user_ids": []},
        ]
        for bad in bad_arguments:
            self.assertTrue(self.call(token, "mark_read", bad)["isError"])

    def test_a_read_only_connection_cannot_mark_read(self) -> None:
        token = self.manual()
        channel_id = get_stream("Denmark", self.user.realm).id
        self.assertTrue(self.call(token, "mark_read", {"channel_id": channel_id})["isError"])

    def test_a_read_only_connection_reads_without_marking_read(self) -> None:
        token = self.manual()
        message_id = self.unread_channel_message()
        sender = self.example_user("othello")
        direct = self.send_personal_message(sender, self.user, "Hello")
        channel_id = get_stream("Denmark", self.user.realm).id

        output = self.call(token, "get_messages", {"channel_id": channel_id, "topic": "reading"})
        self.assertEqual(output["marked_read_count"], 0)
        output = self.call(token, "get_direct_messages", {"user_ids": [sender.id]})
        self.assertEqual(output["marked_read_count"], 0)
        output = self.call(token, "fetch", {"id": f"message:{message_id}"})
        self.assertEqual(output["metadata"]["marked_read_count"], 0)

        self.assertFalse(self.is_read(message_id))
        self.assertFalse(self.is_read(direct))

    def test_reading_marks_read_only_when_mark_read_is_on_auto(self) -> None:
        self.enforce()
        token = self.manual(write=True)
        message_id = self.unread_channel_message()
        channel_id = get_stream("Denmark", self.user.realm).id
        arguments = {"channel_id": channel_id, "topic": "reading"}
        for policy in (OFF, ASK_FIRST):
            set_policies(self.latest_grant(), self.user, {"mark_read": policy})
            output = self.call(token, "get_messages", arguments)
            self.assertEqual(output["marked_read_count"], 0)
            self.assertFalse(self.is_read(message_id))

        set_policies(self.latest_grant(), self.user, {"mark_read": AUTO})
        output = self.call(token, "get_messages", arguments)
        self.assertEqual(output["marked_read_count"], 1)
        self.assertTrue(self.is_read(message_id))

    def test_mark_read_rejects_an_empty_topic_and_marks_nothing(self) -> None:
        token = self.manual(write=True)
        message_id = self.unread_channel_message()
        channel_id = get_stream("Denmark", self.user.realm).id

        result = self.call(token, "mark_read", {"channel_id": channel_id, "topic": ""})

        self.assertTrue(result["isError"])
        self.assertFalse(self.is_read(message_id))
