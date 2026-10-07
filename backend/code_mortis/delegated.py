"""OCR selects scope and rules; the application accounts for each native-client result."""

import json
import time

from pydantic import ValidationError

from . import ocr, runner_client
from .config import settings
from .schemas import ReviewComment
from .security import digest


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


def review(repo, env, base, head, merge_base, connection, options, run_id, stopped, progress, checkpoint):
    ocr.check_version(repo, env)
    deadline = time.monotonic() + options["timeout_seconds"]
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
            raise ValueError("OCR could not prepare the delegated review scope or rules.")
        try:
            data = json.loads(output)
        except ValueError as exc:
            raise ValueError("OCR returned invalid delegation metadata.") from exc
        if not isinstance(data, dict) or data.get("schema_version") != "1":
            raise ValueError("OCR returned an unsupported delegation schema.")
        return data

    preview = delegate("preview")
    selected = preview.get("reviewable_files")
    if not isinstance(selected, list) or len(selected) != preview.get("reviewable_count"):
        raise ValueError("OCR returned inconsistent selected-file coverage.")
    paths = []
    for item in selected:
        path = item.get("path", "")
        if not path or ocr.ReviewComment.relative_path(path) != path or path in paths:
            raise ValueError("OCR returned an invalid or repeated selected path.")
        paths.append(path)
    # Existing checkpoints belong to this immutable run and its exact commit/config snapshot.
    previous = checkpoint.get("items", {})
    items, comments = {}, []
    used = checkpoint.get("context_bytes", sum(item.get("input_bytes", 0) for item in previous.values()))

    def save():
        progress(
            {
                "selected": len(paths),
                "completed": sum(row["status"] == "completed" for row in items.values()),
                "failed": sum(row["status"] == "failed" for row in items.values()),
                "items": dict(items),
                "context_bytes": used,
            }
        )

    for item in selected:
        path = item["path"]
        if stopped():
            raise ocr.ReviewStopped()
        if previous.get(path, {}).get("status") == "completed":
            items[path] = previous[path]
            comments.extend(previous[path]["comments"])
            save()
            continue
        if time.monotonic() >= deadline:
            items[path] = {"status": "failed", "error": "Review time limit reached."}
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
            {
                "task": "Review this complete file and diff. Return findings only for the assigned path.",
                "language": options["language"],
                "background": options.get("background", ""),
                "assigned_path": path,
                "rules": [group["rule"] for group in groups],
                "head_file": source,
                "diff": patch,
                "base_sha": base,
                "head_sha": head,
            },
            ensure_ascii=False,
        )
        input_bytes = len(prompt.encode()) + 4096
        # Native clients do not expose OCR's hard token cap. This separately named limit
        # bounds supplied context; the native account's provider limits still apply.
        if used + input_bytes > options["delegated_context_bytes"] or len(prompt) > 4_000_000:
            items[path] = {
                "status": "failed",
                "error": "Delegated context budget exceeded; increase the repository limit.",
            }
            save()
            continue
        used += input_bytes
        save()  # Reserve failed or interrupted requests too; resuming must not reset consumed context.
        try:
            data = runner_client.review(
                connection["id"],
                run_id + "-" + digest(path)[:16],
                {
                    "kind": connection["kind"],
                    "model": connection["model"],
                    "effort": options["effort"],
                    "prompt": prompt,
                    "output_schema": output_schema(),
                    "timeout": max(10, int(deadline - time.monotonic())),
                },
                stopped,
            )
            found = validate_result(data, path, len(source.splitlines()), patch)
            for comment in found:
                comment["source_sha"] = ref
            items[path] = {"status": "completed", "comments": found, "input_bytes": input_bytes}
            comments.extend(found)
        except runner_client.RunnerBlocked as exc:
            if exc.reason != "failed":
                save()
                raise
            items[path] = {"status": "failed", "error": "The official client did not complete this file."}
        except ValueError:
            items[path] = {
                "status": "failed",
                "error": "The official client returned invalid findings for this file.",
            }
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
            "context_budget": options["delegated_context_bytes"],
            "timeout_seconds": options["timeout_seconds"],
        },
        "warnings": [],
        "summary": {"findings": len(comments)},
    }
    return status, report, comments
