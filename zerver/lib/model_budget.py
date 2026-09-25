"""Model budget state for a realm.

This is a stub. It always reports "ok". A later change replaces the body
with a real check of `AgentRealmSettings.monthly_budget_microunits`
against the model usage of the workspace. The signature stays the same,
so a caller does not change when the real check arrives.
"""

from typing import Literal

from zerver.models import Realm


def model_budget_state(realm: Realm) -> Literal["ok", "warn", "exceeded"]:
    return "ok"
