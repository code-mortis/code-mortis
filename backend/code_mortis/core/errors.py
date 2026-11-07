"""Execution signals shared by workflow, source and legacy adapters."""


class ReviewStopped(Exception):
    pass


class InferenceOutputError(ValueError):
    def __init__(self, message, usage, status="failed", *, feedback=None):
        super().__init__(message)
        self.usage = usage
        self.status = status
        self.feedback = feedback


class RunnerBlocked(ValueError):
    def __init__(self, reason: str, usage=None):
        self.reason = reason
        tokens = usage.get("tokens") if isinstance(usage, dict) else None
        self.usage = {"tokens": tokens if type(tokens) is int and tokens >= 0 else None}
        super().__init__(
            {
                "retired": "Subscription execution is retired. Select a shared API connection and explicitly start new work.",
                "busy": "This run or connection is in use. The review will wait.",
                "quota": "The subscription limit was reached. Retry when usage is available.",
                "authentication": "This personal account needs to sign in again. Reconnect it in Settings, then retry.",
                "prompt_limit": "The subscription prompt exceeds its conservative per-prompt token limit. Increase Prompt ceiling on the AI connection or reduce the supplied context.",
                "token_limit": "The subscription request exceeded the remaining Token budget. Increase Token budget, choose Unlimited, or reduce the review scope.",
                "usage_unknown": "The native client did not report valid token usage. Further AI checks were stopped because the remaining Token budget cannot be verified. Start a new review.",
                "unavailable": "The service that runs ChatGPT and Claude Code is unavailable. Ask the administrator to check the runner service, then try again.",
            }.get(
                reason,
                "ChatGPT or Claude Code could not complete this request. Check the AI connection account status, then try again.",
            )
        )


