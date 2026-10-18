"""Whether a tool call from an external client may run: Auto, Ask first or
Off. The inbound MCP server asks evaluate() before it runs anything; the
outbound gateway is meant to ask the same question with its own tool names.
"""

from collections.abc import Collection, Mapping
from typing import Literal

from django.db import transaction
from django.utils.timezone import now
from django.utils.translation import gettext as _

from zerver.lib.exceptions import JsonableError
from zerver.lib.mcp_tools import DESTRUCTIVE_TOOLS, TOOLS
from zerver.models import (
    AgentRealmSettings,
    MCPAccessGrant,
    MCPAccessToolPolicy,
    McpConnection,
    Realm,
    UserProfile,
)
from zerver.models.mcp import MCP_TOOL_POLICIES

Decision = Literal["allow", "ask", "deny"]
ToolKind = Literal["read", "write", "destructive"]

AUTO = "allow"
ASK_FIRST = "approve"
OFF = "block"

# These write tools say something to other people, or change or remove what
# was said or planned, so a new grant asks before they run.
ASK_FIRST_BY_DEFAULT = frozenset(
    {"send_message", "send_direct_message", "edit_message", "delete_task"}
)

_DECISIONS: dict[str, Decision] = {AUTO: "allow", ASK_FIRST: "ask", OFF: "deny"}


def tool_kind(tool_name: str) -> ToolKind:
    _model, _description, write = TOOLS[tool_name]
    if not write:
        return "read"
    return "destructive" if tool_name in DESTRUCTIVE_TOOLS else "write"


def default_policy(tool_name: str) -> str:
    return ASK_FIRST if tool_name in ASK_FIRST_BY_DEFAULT else AUTO


def scope_allows(tool_name: str, scopes: Collection[str]) -> bool:
    _model, _description, write = TOOLS[tool_name]
    return not write or "team:write" in scopes


def evaluate(
    tool_name: str,
    *,
    scopes: Collection[str],
    mode: str,
    stored: Mapping[str, str],
    unreviewed_new_tool: bool = False,
) -> Decision:
    """The decision for one tool. A tool that does not exist, or that a
    connection has not yet had reviewed, is denied. A legacy grant has the
    access its scopes give it. A managed grant also needs the scope, then
    follows the stored policy, or the class default when none is stored."""
    if tool_name not in TOOLS or unreviewed_new_tool:
        return "deny"
    if not scope_allows(tool_name, scopes):
        return "deny"
    if mode == "legacy":
        return "allow"
    return _DECISIONS[stored.get(tool_name) or default_policy(tool_name)]


def policy_enforced(realm: Realm) -> bool:
    return AgentRealmSettings.objects.filter(realm=realm, mcp_policy_enforced=True).exists()


def stored_policies(grant: MCPAccessGrant) -> dict[str, str]:
    return dict(grant.tool_policies.values_list("tool_name", "policy"))


def evaluate_grant(grant: MCPAccessGrant, tool_name: str) -> Decision:
    return evaluate(
        tool_name,
        scopes=grant.scopes,
        mode=grant.policy_mode,
        stored=stored_policies(grant),
    )


def effective_policy(grant: MCPAccessGrant, tool_name: str, stored: Mapping[str, str]) -> str:
    """The policy a person sees for a tool: what is stored, else what the
    grant does today without one."""
    if grant.policy_mode == "legacy":
        return AUTO
    return stored.get(tool_name) or default_policy(tool_name)


def set_policies(grant: MCPAccessGrant, actor: UserProfile, changes: Mapping[str, str]) -> None:
    """Store the given policies. A legacy grant becomes managed first, with
    every tool it can use kept on Auto, so changing one tool does not
    change the others."""
    for tool_name, policy in changes.items():
        if tool_name not in TOOLS or policy not in MCP_TOOL_POLICIES:
            raise ValueError("Unknown tool or policy.")
    with transaction.atomic():
        grant = MCPAccessGrant.objects.select_for_update(no_key=True).get(id=grant.id)
        if grant.policy_mode == "legacy":
            grant.policy_mode = "managed"
            grant.save(update_fields=["policy_mode"])
            for tool_name in TOOLS:
                if scope_allows(tool_name, grant.scopes):
                    MCPAccessToolPolicy.objects.get_or_create(
                        grant=grant,
                        tool_name=tool_name,
                        defaults={"policy": AUTO, "updated_by": actor},
                    )
        for tool_name, policy in changes.items():
            MCPAccessToolPolicy.objects.update_or_create(
                grant=grant,
                tool_name=tool_name,
                defaults={"policy": policy, "updated_by": actor, "updated_at": now()},
            )


def check_connection_policy_editable(actor: UserProfile, connection: McpConnection) -> None:
    """An outbound connection that an administrator locked keeps its tool
    policies until an administrator changes them."""
    if connection.policy_locked and not actor.is_realm_admin:
        raise JsonableError(
            _("Only an administrator can change this connection while it is locked.")
        )
