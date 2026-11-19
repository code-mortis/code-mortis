"""Execution signals shared by workflows and source readers."""


class ReviewStopped(Exception):
    pass


def engine_stopped(stopped):
    """Keep cancellation outside the library's validation and recovery paths."""
    if stopped():
        raise ReviewStopped("The operation was cancelled or superseded.")
    return False


class InferenceOutputError(ValueError):
    def __init__(self, message, usage, status="failed", *, feedback=None):
        super().__init__(message)
        self.usage = usage
        self.status = status
        self.feedback = feedback


class InferenceBudgetError(InferenceOutputError):
    """A request cannot fit within its remaining budget even after active work settles."""


class RunnerBlocked(ValueError):
    def __init__(self, reason: str, usage=None):
        self.reason = reason
        tokens = usage.get("tokens") if isinstance(usage, dict) else None
        self.usage = {"tokens": tokens if type(tokens) is int and tokens >= 0 else None}
        super().__init__(
            {
                "retired": "Subscription execution is retired. Select a shared API connection and explicitly start new work.",
                "busy": "This run or connection is in use. The review will wait.",
                "quota": "The selected provider limit was reached. Retry when usage is available.",
                "authentication": "The selected connection needs valid credentials. Ask an administrator to update it, then retry.",
                "prompt_limit": "The request exceeds the connection's conservative per-prompt token limit. Increase Prompt ceiling on the AI connection or reduce the supplied context.",
                "token_limit": "The request exceeded the remaining Token budget. Increase Token budget, choose Unlimited, or reduce the review scope.",
                "usage_unknown": "The selected provider did not report valid token usage. Further AI checks were stopped because the remaining Token budget cannot be verified. Start a new review.",
                "unavailable": "The selected provider is unavailable. Check the shared API connection, then try again.",
            }.get(
                reason,
                "The shared API connection could not complete this request. Check its configuration, then try again.",
            )
        )


