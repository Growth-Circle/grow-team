"""The job card's reason codes (spec 13, map-backend 13-R8).

The job detail keeps job_reason_code's precise code. The card names the
cause a person can act on instead: several precise codes share one card
code. A code with no entry here is the same on the card and in the detail.
"""

CARD_REASON_CODES: dict[str, str] = {
    "lease_lost": "runner_offline",
    "stop_unconfirmed": "runner_offline",
    "start_deadline_expired": "runner_offline",
    "budget_exhausted": "budget_exceeded",
    "runtime_stopped": "tool_error",
    "start_failed": "tool_error",
    "result_invalid": "tool_error",
    "approval_expired": "timeout",
    "authority_changed": "grant_revoked",
}

# Card codes that name a decision or a lost permission: a retry cannot
# change them, so the card offers no retry even when the job could resume.
NOT_RETRYABLE_CARD_CODES: frozenset[str] = frozenset(
    {"audience_changed", "profile_needs_action", "grant_revoked", "approval_rejected"}
)


def card_reason_code(reason_code: str | None) -> str | None:
    if reason_code is None:
        return None
    return CARD_REASON_CODES.get(reason_code, reason_code)
