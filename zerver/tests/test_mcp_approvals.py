import json
from datetime import timedelta
from typing import TYPE_CHECKING

from django.utils.timezone import now
from typing_extensions import override

from zerver.actions.tasks import do_create_task
from zerver.lib.exceptions import JsonableError
from zerver.lib.mcp_pending import (
    MAX_PENDING_PER_GRANT,
    RECEIPT_WINDOW,
    argument_digest,
    call_result,
    request_approval,
)
from zerver.lib.mcp_policy import OFF, set_policies
from zerver.lib.tasks import get_or_create_default_board, realm_task_id_prefix
from zerver.lib.test_classes import ZulipTestCase
from zerver.models import MCPAccessAudit, MCPAccessGrant, MCPPendingCall, Message
from zerver.models.streams import get_stream

if TYPE_CHECKING:
    from django.test.client import _MonkeyPatchedWSGIResponse as TestHttpResponse


class MCPApprovalTest(ZulipTestCase):
    @override
    def setUp(self) -> None:
        super().setUp()
        self.owner = self.example_user("hamlet")
        self.grant = MCPAccessGrant.objects.create(
            realm=self.owner.realm,
            user=self.owner,
            name="Claude",
            scopes=["team:read", "team:write"],
            resource="http://zulip.testserver/mcp",
            expires_at=now() + timedelta(days=1),
        )
        self.login_user(self.owner)
        self.subscribe(self.owner, "Denmark")
        self.stream = get_stream("Denmark", self.owner.realm)

    def send_args(self, content: str = "Shipping tonight.") -> dict[str, object]:
        return {
            "channel_id": self.stream.id,
            "topic": "Release",
            "content": content,
            "mention_user_ids": [],
        }

    def ask(self, content: str = "Shipping tonight.") -> MCPPendingCall:
        return request_approval(self.grant, "send_message", self.send_args(content))

    def decide(
        self, call: MCPPendingCall, decision: str = "approve", **overrides: str
    ) -> "TestHttpResponse":
        data = {
            "decision": decision,
            "argument_digest": call.argument_digest,
            "nonce": str(call.nonce),
            **overrides,
        }
        return self.client_post(f"/json/mcp/calls/{call.id}/decision", data)

    def sent_count(self, content: str) -> int:
        return Message.objects.filter(
            realm_id=self.owner.realm_id, content=content, sender=self.owner
        ).count()

    def test_asking_creates_one_pending_call_per_distinct_arguments(self) -> None:
        # AC-V1-008
        first = self.ask()
        self.assertEqual(first.status, "pending")
        self.assertEqual(first.argument_digest, argument_digest("send_message", self.send_args()))
        self.assertEqual(self.sent_count("Shipping tonight."), 0)
        self.assertEqual(self.ask().id, first.id)
        other = self.ask("Another message.")
        self.assertNotEqual(other.id, first.id)
        self.assertEqual(MCPPendingCall.objects.filter(grant=self.grant).count(), 2)
        self.assertEqual(
            list(MCPAccessAudit.objects.filter(grant=self.grant).values_list("outcome", flat=True)),
            ["asked", "asked"],
        )
        self.assertEqual(self.sent_count("Another message."), 0)

    def test_approving_runs_the_stored_arguments_once(self) -> None:
        # AC-V1-009
        call = self.ask()
        result = self.decide(call)
        self.assert_json_success(result)
        call.refresh_from_db()
        self.assertEqual(call.status, "executed")
        self.assertEqual(self.sent_count("Shipping tonight."), 1)
        assert call.result is not None
        message = Message.objects.get(id=call.result["id"])
        self.assertEqual(message.sender_id, self.owner.id)
        self.assertEqual(message.sending_client.name, "Grow Team MCP")
        self.assertEqual(message.topic_name(), "Release")
        audit = MCPAccessAudit.objects.filter(grant=self.grant)
        self.assertEqual(
            list(audit.order_by("id").values_list("outcome", flat=True)),
            ["asked", "approved", "executed"],
        )
        self.assertEqual(
            set(audit.values_list("argument_digest", flat=True)), {call.argument_digest}
        )
        self.assertEqual(set(audit.values_list("actor_id", flat=True)), {self.owner.id})
        self.assertNotIn("Shipping tonight.", json.dumps(list(audit.values("detail", "tool_name"))))

    def test_deciding_twice_does_not_run_twice(self) -> None:
        call = self.ask()
        self.assert_json_success(self.decide(call))
        result = self.decide(call)
        self.assertEqual(result.status_code, 409)
        self.assertEqual(self.sent_count("Shipping tonight."), 1)

    def test_retry_gets_the_pending_notice_then_the_receipt(self) -> None:
        # AC-V1-011
        call = self.ask()
        retry = self.ask()
        self.assertEqual(retry.id, call.id)
        text = call_result(retry)
        self.assertTrue(text["isError"])
        self.assertIn("waiting", text["content"][0]["text"])
        self.assert_json_success(self.decide(call))
        receipt = call_result(self.ask())
        self.assertFalse(receipt["isError"])
        stored = MCPPendingCall.objects.get(id=call.id).result
        assert stored is not None
        self.assertEqual(json.loads(receipt["content"][0]["text"])["id"], stored["id"])
        self.assertEqual(MCPPendingCall.objects.filter(grant=self.grant).count(), 1)
        self.assertEqual(self.sent_count("Shipping tonight."), 1)

    def test_identical_call_after_the_receipt_window_is_a_new_request(self) -> None:
        call = self.ask()
        self.assert_json_success(self.decide(call))
        MCPPendingCall.objects.filter(id=call.id).update(
            decided_at=now() - RECEIPT_WINDOW - timedelta(seconds=1)
        )
        again = self.ask()
        self.assertNotEqual(again.id, call.id)
        self.assertEqual(again.status, "pending")

    def test_only_the_grant_owner_decides(self) -> None:
        # AC-V1-010
        call = self.ask()
        self.login("iago")
        result = self.decide(call)
        self.assertEqual(result.status_code, 403)
        result = self.decide(call, "reject")
        self.assertEqual(result.status_code, 403)
        call.refresh_from_db()
        self.assertEqual(call.status, "pending")
        self.assertEqual(self.sent_count("Shipping tonight."), 0)

    def test_a_changed_digest_nonce_or_stored_argument_does_not_run(self) -> None:
        # AC-V1-010
        call = self.ask()
        self.assertEqual(self.decide(call, argument_digest="0" * 64).status_code, 409)
        self.assertEqual(self.decide(call, nonce="not-it").status_code, 409)
        MCPPendingCall.objects.filter(id=call.id).update(
            arguments={**self.send_args(), "content": "Something else."}
        )
        self.assertEqual(self.decide(call).status_code, 409)
        call.refresh_from_db()
        self.assertEqual(call.status, "pending")
        self.assertEqual(
            Message.objects.filter(
                realm_id=self.owner.realm_id,
                sender=self.owner,
                content__in=["Something else.", "Shipping tonight."],
            ).count(),
            0,
        )

    def test_an_expired_call_does_not_run(self) -> None:
        # AC-V1-010
        call = self.ask()
        MCPPendingCall.objects.filter(id=call.id).update(expires_at=now() - timedelta(seconds=1))
        self.assertEqual(self.decide(call).status_code, 409)
        call.refresh_from_db()
        self.assertEqual(call.status, "expired")
        self.assertEqual(self.sent_count("Shipping tonight."), 0)
        self.assertNotEqual(self.ask().id, call.id)

    def test_the_call_expires_after_the_workspace_approval_ttl(self) -> None:
        from zerver.models import AgentRealmSettings

        AgentRealmSettings.objects.create(realm=self.owner.realm, approval_ttl_minutes=30)
        call = self.ask()
        self.assertAlmostEqual(
            (call.expires_at - call.created_at).total_seconds(), 30 * 60, delta=5
        )

    def test_a_tool_turned_off_after_asking_does_not_run(self) -> None:
        call = self.ask()
        set_policies(self.grant, self.owner, {"send_message": OFF})
        result = self.decide(call)
        self.assertEqual(result.status_code, 409)
        call.refresh_from_db()
        self.assertEqual(call.status, "rejected")
        self.assertEqual(self.sent_count("Shipping tonight."), 0)
        self.assertEqual(MCPAccessAudit.objects.filter(outcome="blocked").count(), 1)

    def test_a_revoked_grant_does_not_run(self) -> None:
        call = self.ask()
        MCPAccessGrant.objects.filter(id=self.grant.id).update(revoked_at=now())
        self.assertEqual(self.decide(call).status_code, 409)
        self.assertEqual(self.sent_count("Shipping tonight."), 0)

    def test_rejecting_keeps_the_call_from_running(self) -> None:
        call = self.ask()
        self.assert_json_success(self.decide(call, "reject"))
        call.refresh_from_db()
        self.assertEqual(call.status, "rejected")
        self.assertEqual(self.sent_count("Shipping tonight."), 0)
        self.assertTrue(MCPAccessAudit.objects.filter(outcome="declined").exists())
        retry = call_result(self.ask())
        self.assertTrue(retry["isError"])
        self.assertIn("declined", retry["content"][0]["text"])
        self.assertEqual(self.decide(call).status_code, 409)

    def test_a_failing_approved_call_is_recorded_and_not_retried(self) -> None:
        message_id = self.send_stream_message(self.owner, "Denmark")
        call = request_approval(
            self.grant, "add_reaction", {"message_id": message_id, "emoji_name": "tada"}
        )
        # The reaction was added by other means after the call was asked for.
        self.assert_json_success(
            self.api_post(
                self.owner, f"/api/v1/messages/{message_id}/reactions", {"emoji_name": "tada"}
            )
        )
        self.assert_json_success(self.decide(call))
        call.refresh_from_db()
        self.assertEqual(call.status, "failed")
        outcomes = list(
            MCPAccessAudit.objects.filter(grant=self.grant)
            .order_by("id")
            .values_list("outcome", flat=True)
        )
        self.assertEqual(outcomes, ["asked", "approved", "rejected"])
        self.assertTrue(call_result(call)["isError"])
        self.assertEqual(self.decide(call).status_code, 409)

    def test_a_target_the_account_cannot_reach_is_refused_when_asked(self) -> None:
        stream = self.make_stream("private-room", invite_only=True)
        with self.assertRaises(JsonableError):
            request_approval(
                self.grant,
                "send_message",
                {"channel_id": stream.id, "topic": "x", "content": "hi", "mention_user_ids": []},
            )
        self.assertFalse(MCPPendingCall.objects.exists())

    def test_too_many_waiting_calls_are_refused(self) -> None:
        for index in range(MAX_PENDING_PER_GRANT):
            self.ask(f"Message {index}")
        with self.assertRaises(JsonableError):
            self.ask("One more")
        self.assertEqual(MCPPendingCall.objects.count(), MAX_PENDING_PER_GRANT)

    def test_preview_names_the_target_and_keeps_the_full_content(self) -> None:
        # AC-V1-012 (data side)
        recipient = self.example_user("othello")
        content = "x" * 1500
        call = request_approval(
            self.grant,
            "send_message",
            {**self.send_args(content), "mention_user_ids": [recipient.id]},
        )
        self.assertEqual(
            call.preview,
            {
                "tool": "send_message",
                "target": {
                    "type": "channel",
                    "name": "Denmark",
                    "topic": "Release",
                    "message_id": None,
                },
                "content": content,
                "mentions": [recipient.full_name],
            },
        )
        direct = request_approval(
            self.grant,
            "send_direct_message",
            {"recipient_user_ids": [recipient.id], "content": "hi", "mention_user_ids": []},
        )
        self.assertEqual(direct.preview["target"]["name"], recipient.full_name)
        self.assertEqual(direct.preview["target"]["type"], "direct")

    def test_preview_of_message_and_file_tools(self) -> None:
        message_id = self.send_stream_message(self.owner, "Denmark", "original", "Topic A")
        edit = request_approval(
            self.grant, "edit_message", {"message_id": message_id, "content": "changed"}
        )
        self.assertEqual(
            edit.preview["target"],
            {"type": "message", "name": "Denmark", "topic": "Topic A", "message_id": message_id},
        )
        self.assertEqual(edit.preview["content"], "changed")
        reaction = request_approval(
            self.grant, "add_reaction", {"message_id": message_id, "emoji_name": "tada"}
        )
        self.assertEqual(reaction.preview["content"], ":tada:")
        other_message = self.send_stream_message(self.example_user("othello"), "Denmark")
        with self.assertRaises(ValueError):
            request_approval(
                self.grant, "edit_message", {"message_id": other_message, "content": "no"}
            )
        upload = request_approval(
            self.grant,
            "upload_file",
            {"filename": "a.txt", "content_base64": "aGVsbG8=", "content_type": "text/plain"},
        )
        self.assertEqual(upload.preview["target"]["name"], "a.txt")
        self.assertEqual(upload.preview["content"], "text/plain, 5 bytes")

    def test_preview_of_task_tools_names_the_card_column_and_assignee(self) -> None:
        board = get_or_create_default_board(self.owner.realm)
        column, other_column = board.columns.all()[:2]
        task = do_create_task(
            user_profile=self.owner, board=board, column=column, title="Lock the feature list"
        )
        prefix = realm_task_id_prefix(self.owner.realm)
        label = f"{prefix}-{task.counter} Lock the feature list"
        delete = request_approval(self.grant, "delete_task", {"task_id": task.id})
        self.assertEqual(
            delete.preview["target"],
            {"type": "task", "name": label, "topic": None, "message_id": None},
        )
        self.assertEqual(delete.preview["content"], "")
        othello = self.example_user("othello")
        update = request_approval(
            self.grant,
            "update_task",
            {
                "task_id": task.id,
                "column_id": other_column.id,
                "assignee_id": othello.id,
                "labels": ["urgent", "ops"],
                "blocked": True,
                "clear_due_at": False,
            },
        )
        self.assertEqual(update.preview["target"]["name"], label)
        self.assertEqual(
            update.preview["content"].split("\n"),
            [
                f"column: {other_column.name}",
                f"assignee: {othello.full_name}",
                "labels: urgent, ops",
                "blocked: True",
            ],
        )
        create = request_approval(
            self.grant,
            "create_task",
            {"title": "Plan the launch", "column_id": column.id, "body": "Draft the steps."},
        )
        self.assertEqual(
            create.preview["target"],
            {"type": "column", "name": column.name, "topic": None, "message_id": None},
        )
        self.assertEqual(create.preview["content"], "Plan the launch\nDraft the steps.")
        with self.assertRaises(JsonableError):
            request_approval(self.grant, "create_task", {"title": "x", "column_id": 999999999})

    def test_preview_of_a_needs_tool_lists_the_ids(self) -> None:
        call = request_approval(
            self.grant, "resolve_needs", {"need_ids": ["mention:5", "card_mention:7"]}
        )
        self.assertEqual(
            call.preview,
            {
                "tool": "resolve_needs",
                "target": {"type": "needs", "name": "Needs you", "topic": None, "message_id": None},
                "content": "mention:5\ncard_mention:7",
                "mentions": [],
            },
        )
