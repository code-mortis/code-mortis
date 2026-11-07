"""Safe, structured indexing failures; never copy subprocess or HTTP response bodies."""

from code_mortis.schemas.code_graph import GraphFailure


class GraphError(ValueError):
    def __init__(self, code, stage, message, recovery, **measurements):
        self.failure = GraphFailure(code=code, stage=stage, message=message, recovery=recovery,
                                    measurements=measurements)
        super().__init__(message + " " + recovery)


def measured(code, actual, limit):
    if code == "file_count":
        message = f"{actual:,} eligible files exceed the {limit:,}-file limit."
        recovery = "Increase Maximum indexed files or add exclusions."
    elif code == "source_size":
        message = f"Eligible source totals {actual / 1024**2:.1f} MiB; the limit is {limit / 1024**2:g} MiB."
        recovery = "Increase Maximum total source size or add exclusions."
    elif code == "tree_limit":
        message = f"The Git tree has {actual:,} entries; the infrastructure limit is {limit:,}."
        recovery = "Reduce the repository tree before retrying. Exclusions do not change this safety limit."
    else:
        message = f"Graph object storage uses {actual:,} bytes; its disk limit is {limit:,} bytes."
        recovery = "Ask the administrator to free graph storage, then retry."
    return GraphError(code, "admission" if code != "storage_limit" else "git_fetch", message, recovery,
                      actual=actual, limit=limit)


PARSER_ERRORS = {
    "parser_unavailable": ("The indexing worker could not connect to the parser.", "Check the parser service, then retry."),
    "parser_authentication": ("The parser rejected the worker's credentials.", "Check that worker and parser use the same token, then retry."),
    "parser_busy": ("The parser is already processing another index.", "Wait for that attempt to finish, then retry."),
    "parser_timeout": ("The parser exceeded the indexing deadline.", "Reduce the source scope or check parser resources, then retry."),
    "parser_terminated": ("The parser process stopped before returning a result.", "Check parser logs and resource limits, then retry."),
    "parser_storage_limit": ("The parser ran out of temporary storage.", "Check parser temporary storage or reduce the source scope, then retry."),
    "parser_invalid_result": ("The parser could not produce a valid, bounded result.", "Check matching worker and parser versions or reduce the source scope, then retry."),
    "parser_invalid_request": ("The parser rejected the indexing request.", "Deploy matching worker and parser versions, then retry."),
    "transport_limit": ("The indexing payload exceeds the transport safety limit.", "Reduce the source scope, then retry."),
    "cancelled": ("Graph indexing was cancelled or its configuration changed.", "Retry from Code graph settings to resume indexing."),
    "git_access_denied": ("GitHub denied access to the repository or revision.", "Check the GitHub App installation and Contents read permission, then retry."),
    "git_objects_unavailable": ("Git could not fetch the requested revision.", "Check that the revision exists and GitHub is reachable, then retry."),
}


def failure(code, stage="parser", **measurements):
    return GraphError(code, stage, *PARSER_ERRORS[code], **measurements)


def describe(exc, stage, reference):
    if isinstance(exc, GraphError):
        result = exc.failure.model_copy()
    else:
        result = GraphFailure(code="unexpected", stage=stage,
                              message=f"Indexing failed during {stage.replace('_', ' ')}.",
                              recovery="Ask the administrator to check logs for this attempt, then retry.")
    result.attempt_reference = reference
    return result
