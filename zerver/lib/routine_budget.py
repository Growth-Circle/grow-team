"""Whether a routine may run, as far as the budget goes. The scheduler asks
here before it starts a job, so a per-agent or per-routine budget can
replace the check below without a change to the scheduler."""

from typing import Literal

from zerver.lib.model_budget import model_budget_state
from zerver.models import AgentRoutine


def gate(routine: AgentRoutine) -> Literal["ok", "warn", "exceeded"]:
    return model_budget_state(routine.realm)
