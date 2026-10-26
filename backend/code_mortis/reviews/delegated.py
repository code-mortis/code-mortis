"""OCR selects scope and rules; the application accounts for each native-client result."""

import json
import time

from pydantic import ValidationError

from code_mortis.core.config import settings
from code_mortis.core.security import digest
from code_mortis.integrations import ocr, runner_client
from code_mortis.reviews import context as review_context
from code_mortis.reviews import graph_context
from code_mortis.reviews import policy as review_policy
from code_mortis.schemas.common import ReviewComment, native_prompt_size


def output_schema():
    item = ReviewComment.model_json_schema()
    item["additionalProperties"] = False
    item["required"] = list(item["properties"])
    for value in item["properties"].values():
        value.pop("default", None)
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["comments"],
        "properties": {"comments": {"type": "array", "items": item}},
    }


def validate_result(data, path, line_count, patch):
    if (
        not isinstance(data, dict)
        or not isinstance(data.get("comments"), list)
        or len(data["comments"]) > 500
    ):
        raise ValueError("The native client returned an invalid findings list.")
    comments = []
    for value in data["comments"]:
        try:
            comment = ReviewComment.model_validate(value).model_dump()
        except ValidationError as exc:
            raise ValueError("The native client returned a malformed finding.") from exc
        if comment["path"] != path or comment["end_line"] > line_count:
            raise ValueError("The native client returned a finding outside its assigned file or lines.")
        comment["inline_line"] = comment["end_line"] if comment["end_line"] in ocr.diff_lines(patch) else None
        comments.append(comment)
    return comments


def review(repo, env, base, head, merge_base, connection, options, run_id, stopped, progress, checkpoint, *, before_analysis=None, credentials=None, graph_options=None):
    ocr.check_version(repo, env)
    deadline = time.monotonic() + options["timeout_seconds"] if options["timeout_seconds"] is not None else None
    args = ["--repo", str(repo), "--from", base, "--to", head, "--format", "json"]
    if options.get("exclusions"):
        args += ["--exclude", options["exclusions"]]
    if options.get("rules") is not None:
        rules = repo.parent / "rules.json"
        rules.write_text(json.dumps(options["rules"]))
        args += ["--rule", str(rules)]
    if options.get("background"):
        background = repo.parent / "background.md"
        background.write_text(options["background"])
        args += ["--background-file", str(background)]

    def delegate(subcommand, paths=()):
        code, output, _ = ocr.command(
            [settings().ocr_binary, "delegate", subcommand, *args, *paths],
            repo,
            env,
            timeout=60,
            stopped=stopped,
        )
        if code:
            raise ValueError("The review engine could not prepare files and rules for ChatGPT or Claude Code. Check repository review settings, then start a new review.")
        try:
            data = json.loads(output)
        except ValueError as exc:
            raise ValueError("The review engine returned unreadable file assignments for ChatGPT or Claude Code. Ask the administrator to check the OCR version and worker logs.") from exc
        if not isinstance(data, dict) or data.get("schema_version") != "1":
            raise ValueError("The review engine returned file assignments that this version of Code Mortis cannot read. Ask the administrator to check the OCR version.")
        return data

    preview = delegate("preview")
    selected = preview.get("reviewable_files")
    paths = ocr.preview_paths(selected, preview.get("reviewable_count"))
    limited = ocr.file_limit_result(paths, options.get("max_files"))
    if before_analysis:
        before_analysis(limited)
    if limited:
        if isinstance(preview.get("excluded_files"), list):
            limited[1]["excluded_files"] = preview["excluded_files"]
        return limited
    # Existing checkpoints belong to this immutable run and its exact commit/config snapshot.
    previous = checkpoint.get("items", {})
    graph_selections = dict(checkpoint.get("code_graph", {})) if graph_options else {}
    items, comments = {}, []
    used = checkpoint.get("context_bytes", sum(item.get("input_bytes", 0) for item in previous.values()))
    tokens = checkpoint.get("total_tokens", None if previous or used else 0)
    if type(tokens) is not int or tokens < 0:
        tokens = None
    budget = options["token_budget"]
    budget_exceeded = False

    def save():
        progress(
            {
                "selected": len(paths),
                "completed": sum(row["status"] == "completed" for row in items.values()),
                "failed": sum(row["status"] == "failed" for row in items.values()),
                "items": dict(items),
                "context_bytes": used,
                "total_tokens": tokens,
                **({"code_graph": dict(graph_selections)} if graph_options else {}),
            }
        )

    for item in selected:
        path = item["path"]
        if stopped():
            raise ocr.ReviewStopped()
        if previous.get(path, {}).get("status") == "completed":
            items[path] = review_context.sanitize(previous[path], credentials or {}, stopped)
            comments.extend(items[path]["comments"])
            save()
            continue
        if deadline is not None and time.monotonic() >= deadline:
            items[path] = {"status": "failed", "error": "Review time limit reached."}
            save()
            continue
        if budget is not None and (tokens is None or tokens >= budget):
            budget_exceeded |= tokens is not None
            items[path] = {"status": "failed", "error": (
                "Token usage is unavailable or invalid; remaining budget cannot be verified. Start a new review."
                if tokens is None else f"Token budget exhausted: {tokens:,} / {budget:,} tokens used."
            )}
            save()
            continue
        patch = ocr.git(
            repo,
            env,
            "diff",
            "--no-ext-diff",
            "--no-textconv",
            "--unified=5",
            f"{merge_base}..{head}",
            "--",
            path,
            stopped=stopped,
        )
        # Reading git objects cannot follow symlinks, run filters, hooks, or scripts from the PR.
        ref = merge_base if item.get("status") in {"D", "deleted"} else head
        source = ocr.git(repo, env, "show", f"{ref}:{path}", stopped=stopped)
        groups = delegate("rule", [path]).get("groups", [])
        if not groups or any(
            group.get("files") != [path] or not isinstance(group.get("rule"), str) for group in groups
        ):
            raise ValueError("OCR rules did not match the file assigned by the application.")
        prompt = json.dumps(
            review_context.sanitize({
                "task": "Review this complete file and diff. Return findings only for the assigned path.",
                "language": options["language"],
                "background": options.get("background", ""),
                **({"review_policy": review_policy.instructions(options)} if "review_sensitivity" in options else {}),
                "assigned_path": path,
                "rules": [group["rule"] for group in groups],
                "head_file": source,
                "diff": patch,
                "base_sha": base,
                "head_sha": head,
            }, credentials or {}, stopped),
            ensure_ascii=False,
        )
        if graph_options and len(prompt) <= 4_000_000:
            if ref != head:
                selection, related = {"reason": "deleted_file", "files": [], "source_bytes": 0}, None
            else:
                remaining = graph_context.MAX_RUN_BYTES - sum(
                    value.get("reserved_bytes", 0) for key, value in graph_selections.items() if key != path
                )
                selection, related = graph_context.prepare(
                    graph_options, path, head, repo, env, options, credentials or {}, stopped,
                    remaining=remaining, saved=graph_selections.get(path),
                )
            if related:
                enriched = json.dumps({**json.loads(prompt), "code_graph": related}, ensure_ascii=False)
                ceiling = connection.get("options", {}).get("prompt_token_ceiling", 58888)
                if len(enriched) <= 4_000_000 and native_prompt_size(enriched, output_schema()) <= ceiling * 4 // 5:
                    prompt = enriched
                else:
                    selection, _ = graph_context.omitted(selection, "prompt_limit")
            graph_selections[path] = selection
        input_bytes = len(prompt.encode()) + 4096
        if len(prompt) > 4_000_000:
            items[path] = {
                "status": "failed",
                "error": "Source context exceeds the native request size limit; reduce the review scope.",
            }
            save()
            continue
        used += input_bytes
        prior_tokens, tokens = tokens, None
        spent = {"tokens": None}
        save()  # Reserve failed or interrupted requests too; resuming must not reset consumed context.
        try:
            data, spent = runner_client.review(
                connection["id"],
                run_id + "-" + digest(path)[:16],
                {
                    "kind": connection["kind"],
                    "model": connection["model"],
                    "effort": options["effort"],
                    "prompt": prompt,
                    "output_schema": output_schema(),
                    "prompt_token_ceiling": connection.get("options", {}).get("prompt_token_ceiling", 58888),
                    "token_budget": None if budget is None else budget - prior_tokens,
                    "timeout": max(1, int(deadline - time.monotonic())) if deadline is not None else None,
                },
                stopped,
            )
            found = review_context.sanitize(validate_result(data, path, len(source.splitlines()), patch), credentials or {}, stopped, usage=spent)
            for comment in found:
                comment["source_sha"] = ref
            items[path] = {"status": "completed", "comments": found, "input_bytes": input_bytes}
            comments.extend(found)
        except runner_client.RunnerBlocked as exc:
            spent = exc.usage
            if exc.reason not in {"failed", "prompt_limit", "token_limit", "usage_unknown"}:
                raise
            budget_exceeded |= exc.reason == "token_limit"
            items[path] = {"status": "failed", "error": str(exc) if exc.reason != "failed" else "The official client did not complete this file."}
        except ValueError:
            items[path] = {
                "status": "failed",
                "error": "The official client returned invalid findings for this file.",
            }
        finally:
            count = spent["tokens"]
            tokens = prior_tokens + count if prior_tokens is not None and type(count) is int and count >= 0 else None
            if path in items:
                items[path]["tokens"] = count
            save()
    completed = [path for path, value in items.items() if value["status"] == "completed"]
    failed = [path for path, value in items.items() if value["status"] == "failed"]
    status = (
        "skipped" if not paths else "partial" if failed and completed else "failed" if failed else "completed"
    )

    def entries(values):
        return [{"item_id": digest(path), "path": path} for path in values]

    report = {
        "manifest": {
            "schema_version": "code-mortis.delegated/v1",
            "terminal_state": status,
            "coverage": {
                "selected": entries(paths),
                "completed": entries(completed),
                "failed": entries(failed),
                "reused": [],
                "waived": [],
            },
        },
        "engine": connection["kind"],
        "ocr_version": settings().ocr_version,
        "excluded_files": preview.get("excluded_files", []),
        "errors": [{"path": path, "message": items[path]["error"]} for path in failed],
        "limits": {
            "context_bytes": used,
            "token_budget": budget,
            "timeout_seconds": options["timeout_seconds"],
        },
        "warnings": [],
        **({"code_graph": {"version": graph_context.VERSION, "files": graph_selections}} if graph_options else {}),
        "summary": {"findings": len(comments), "total_tokens": tokens,
                    "budget_exceeded": budget_exceeded or (budget is not None and tokens is not None and tokens > budget)},
    }
    return status, report, comments
