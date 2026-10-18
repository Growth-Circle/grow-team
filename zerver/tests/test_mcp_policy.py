from datetime import timedelta

from django.utils.timezone import now

from zerver.lib.exceptions import JsonableError
from zerver.lib.mcp_policy import (
    ASK_FIRST,
    ASK_FIRST_BY_DEFAULT,
    AUTO,
    OFF,
    check_connection_policy_editable,
    default_policy,
    effective_policy,
    evaluate,
    evaluate_grant,
    policy_enforced,
    set_policies,
    tool_kind,
)
from zerver.lib.mcp_tools import DESTRUCTIVE_TOOLS, NON_IDEMPOTENT_TOOLS, TOOLS, tool_list
from zerver.lib.test_classes import ZulipTestCase
from zerver.models import AgentRealmSettings, MCPAccessGrant, MCPAccessToolPolicy
from zerver.models.mcp import McpConnection, McpServer

READ_TOOLS = sorted(name for name, (_model, _description, write) in TOOLS.items() if not write)
WRITE_TOOLS = sorted(name for name, (_model, _description, write) in TOOLS.items() if write)
READ_ONLY = ["team:read"]
READ_WRITE = ["team:read", "team:write"]


class MCPPolicyTest(ZulipTestCase):
    def make_grant(self, *, scopes: list[str], mode: str = "managed") -> MCPAccessGrant:
        user = self.example_user("hamlet")
        return MCPAccessGrant.objects.create(
            realm=user.realm,
            user=user,
            name="Claude",
            scopes=scopes,
            resource="http://zulip.testserver/mcp",
            expires_at=now() + timedelta(days=1),
            policy_mode=mode,
        )

    def test_managed_grant_without_rows_follows_class_defaults(self) -> None:
        # AC-V1-001
        for tool_name in READ_TOOLS:
            self.assertEqual(
                evaluate(tool_name, scopes=READ_WRITE, mode="managed", stored={}), "allow"
            )
        for tool_name in WRITE_TOOLS:
            expected = "ask" if tool_name in ASK_FIRST_BY_DEFAULT else "allow"
            self.assertEqual(
                evaluate(tool_name, scopes=READ_WRITE, mode="managed", stored={}), expected
            )
        self.assertEqual(
            {name for name in WRITE_TOOLS if default_policy(name) == ASK_FIRST},
            {"send_message", "send_direct_message", "edit_message", "delete_task"},
        )

    def test_managed_grant_returns_the_stored_policy(self) -> None:
        # AC-V1-001: scope x stored policy
        expected = {AUTO: "allow", ASK_FIRST: "ask", OFF: "deny"}
        for tool_name in READ_TOOLS + WRITE_TOOLS:
            for policy, decision in expected.items():
                for scopes in (READ_ONLY, READ_WRITE):
                    _model, _description, write = TOOLS[tool_name]
                    result = evaluate(
                        tool_name, scopes=scopes, mode="managed", stored={tool_name: policy}
                    )
                    if write and scopes == READ_ONLY:
                        self.assertEqual(result, "deny")
                    else:
                        self.assertEqual(result, decision)

    def test_legacy_grant_allows_what_its_scopes_allowed(self) -> None:
        # AC-V1-002: stored rows and class defaults do not matter.
        stored = dict.fromkeys(TOOLS, OFF)
        for tool_name in READ_TOOLS:
            self.assertEqual(
                evaluate(tool_name, scopes=READ_ONLY, mode="legacy", stored=stored), "allow"
            )
        for tool_name in WRITE_TOOLS:
            self.assertEqual(
                evaluate(tool_name, scopes=READ_ONLY, mode="legacy", stored=stored), "deny"
            )
            self.assertEqual(
                evaluate(tool_name, scopes=READ_WRITE, mode="legacy", stored=stored), "allow"
            )

    def test_unknown_or_unreviewed_tool_is_denied(self) -> None:
        # AC-V1-003
        for mode in ("managed", "legacy"):
            self.assertEqual(
                evaluate("drop_database", scopes=READ_WRITE, mode=mode, stored={}), "deny"
            )
            self.assertEqual(
                evaluate(
                    "get_me",
                    scopes=READ_WRITE,
                    mode=mode,
                    stored={"get_me": AUTO},
                    unreviewed_new_tool=True,
                ),
                "deny",
            )

    def test_new_grant_is_managed_and_existing_rows_are_legacy(self) -> None:
        self.assertEqual(self.make_grant(scopes=READ_ONLY).policy_mode, "managed")
        grant = self.make_grant(scopes=READ_WRITE)
        self.assertEqual(MCPAccessGrant.objects.get(id=grant.id).policy_mode, "managed")
        # What the migration does to a row that predates the column.
        MCPAccessGrant.objects.filter(id=grant.id).update(policy_mode="legacy")
        grant.refresh_from_db()
        self.assertEqual(grant.policy_mode, "legacy")
        self.assertEqual(evaluate_grant(grant, "delete_task"), "allow")

    def test_evaluate_grant_reads_stored_rows(self) -> None:
        grant = self.make_grant(scopes=READ_WRITE)
        self.assertEqual(evaluate_grant(grant, "send_message"), "ask")
        MCPAccessToolPolicy.objects.create(grant=grant, tool_name="send_message", policy=AUTO)
        MCPAccessToolPolicy.objects.create(grant=grant, tool_name="get_me", policy=OFF)
        self.assertEqual(evaluate_grant(grant, "send_message"), "allow")
        self.assertEqual(evaluate_grant(grant, "get_me"), "deny")

    def test_set_policies_adopts_a_legacy_grant_without_changing_other_tools(self) -> None:
        grant = self.make_grant(scopes=READ_WRITE, mode="legacy")
        actor = self.example_user("hamlet")
        set_policies(grant, actor, {"send_message": ASK_FIRST})
        grant.refresh_from_db()
        self.assertEqual(grant.policy_mode, "managed")
        self.assertEqual(evaluate_grant(grant, "send_message"), "ask")
        for tool_name in TOOLS:
            if tool_name != "send_message":
                self.assertEqual(evaluate_grant(grant, tool_name), "allow", tool_name)
        row = MCPAccessToolPolicy.objects.get(grant=grant, tool_name="send_message")
        self.assertEqual(row.updated_by_id, actor.id)

    def test_set_policies_keeps_a_read_only_legacy_grant_away_from_write_tools(self) -> None:
        grant = self.make_grant(scopes=READ_ONLY, mode="legacy")
        set_policies(grant, self.example_user("hamlet"), {"get_me": OFF})
        self.assertEqual(
            set(grant.tool_policies.values_list("tool_name", flat=True)), set(READ_TOOLS)
        )
        for tool_name in WRITE_TOOLS:
            self.assertEqual(evaluate_grant(grant, tool_name), "deny")

    def test_set_policies_rejects_an_unknown_tool_or_policy(self) -> None:
        grant = self.make_grant(scopes=READ_WRITE)
        actor = self.example_user("hamlet")
        with self.assertRaises(ValueError):
            set_policies(grant, actor, {"nope": AUTO})
        with self.assertRaises(ValueError):
            set_policies(grant, actor, {"get_me": "maybe"})
        self.assertFalse(grant.tool_policies.exists())

    def test_effective_policy_for_display(self) -> None:
        legacy = self.make_grant(scopes=READ_WRITE, mode="legacy")
        managed = self.make_grant(scopes=READ_WRITE)
        self.assertEqual(effective_policy(legacy, "send_message", {}), AUTO)
        self.assertEqual(effective_policy(managed, "send_message", {}), ASK_FIRST)
        self.assertEqual(effective_policy(managed, "send_message", {"send_message": OFF}), OFF)

    def test_tool_kind_and_hints(self) -> None:
        # AC-V1-005
        self.assertEqual(tool_kind("get_me"), "read")
        self.assertEqual(tool_kind("send_message"), "write")
        self.assertEqual(tool_kind("delete_task"), "destructive")
        self.assertEqual(tool_kind("edit_message"), "destructive")
        advertised = {tool["name"]: tool["annotations"] for tool in tool_list(READ_WRITE)}
        for tool_name, annotations in advertised.items():
            assert isinstance(annotations, dict)
            self.assertEqual(annotations["destructiveHint"], tool_name in DESTRUCTIVE_TOOLS)
            self.assertEqual(annotations["idempotentHint"], tool_name not in NON_IDEMPOTENT_TOOLS)
            self.assertEqual(annotations["readOnlyHint"], tool_name in READ_TOOLS)
        self.assertEqual(DESTRUCTIVE_TOOLS, {"delete_task", "edit_message"})
        self.assertFalse(advertised["send_message"]["idempotentHint"])  # type: ignore[index]
        self.assertTrue(advertised["add_reaction"]["idempotentHint"])  # type: ignore[index]
        self.assertEqual(
            [tool["name"] for tool in tool_list(READ_WRITE, only={"get_me"})], ["get_me"]
        )

    def test_policy_enforced_follows_the_realm_flag(self) -> None:
        realm = self.example_user("hamlet").realm
        self.assertFalse(policy_enforced(realm))
        settings, _created = AgentRealmSettings.objects.get_or_create(realm=realm)
        self.assertFalse(settings.mcp_policy_enforced)
        AgentRealmSettings.objects.filter(realm=realm).update(mcp_policy_enforced=True)
        self.assertTrue(policy_enforced(realm))

    def test_locked_connection_can_only_be_changed_by_an_administrator(self) -> None:
        # AC-V1-014 (the rule; there is no outbound API to call it from yet)
        admin = self.example_user("iago")
        member = self.example_user("hamlet")
        server = McpServer.objects.create(
            realm=admin.realm, slug="docs", name="Docs", added_by=admin
        )
        connection = McpConnection.objects.create(
            realm=admin.realm, server=server, created_by=admin
        )
        self.assertFalse(connection.policy_locked)
        check_connection_policy_editable(member, connection)
        connection.policy_locked = True
        connection.save()
        with self.assertRaises(JsonableError):
            check_connection_policy_editable(member, connection)
        check_connection_policy_editable(admin, connection)
