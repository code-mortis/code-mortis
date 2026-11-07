import fcntl
import json
import os
import re
import shutil
import tempfile
import time
from collections.abc import Callable
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

from pydantic import ValidationError

from code_mortis.core import file_policy, inference_admission, process
from code_mortis.core import source as git_source
from code_mortis.core.config import settings
from code_mortis.core.errors import ReviewStopped
from code_mortis.core.review_results import fingerprint as fingerprint
from code_mortis.core.review_results import report_tokens as report_tokens
from code_mortis.core.security import clean_text, digest, safe_endpoint
from code_mortis.reviews import policy as review_policy
from code_mortis.schemas.common import ReviewComment

MAX_OUTPUT = 20_000_000


# Compatibility exports for legacy adapters and historical tests.
child_environment = process.child_environment
git = git_source.git


def command(args, directory, env, timeout=60, stopped=lambda: False, poll=lambda: None, *, binary=False):
    if args and args[0] == settings().ocr_binary and any(part in {"review", "scan"} for part in args[1:2]):
        env = {**env, "CM_OCR_REDACTION_FILE": str(Path.home() / ".opencodereview" / "redaction.json")}
    return process.command(args, directory, env, timeout, stopped, poll, binary=binary, max_output=MAX_OUTPUT)


@contextmanager
def configuration(connection: dict, credentials: dict, review_settings: dict, stopped=lambda: False,
                  *, session_per_task=False, structured_result=False):
    if not settings().ocr_isolated_runtime:
        raise ValueError("OCR execution requires the isolated Docker worker (CM_OCR_ISOLATED_RUNTIME=true).")
    if connection.get("kind", "api") != "api" or not isinstance(connection.get("id"), str) or not connection["id"]:
        raise ValueError("OCR inference requires an existing shared API connection snapshot.")
    try:
        with inference_admission.reserve_connection(
            connection["id"], "ocr:" + str(uuid4()), stopped=stopped,
            wait_seconds=review_settings.get("timeout_seconds") or 900, lease_seconds=3600,
        ):
            with _configuration(connection, credentials, review_settings, stopped,
                                session_per_task=session_per_task, structured_result=structured_result) as update:
                yield update
    except inference_admission.AdmissionStopped as exc:
        raise ReviewStopped("Review cancelled while waiting for shared connection capacity.") from exc


@contextmanager
def _configuration(connection: dict, credentials: dict, review_settings: dict, stopped=lambda: False,
                   *, session_per_task=False, structured_result=False):
    if not settings().ocr_isolated_runtime:
        raise ValueError("OCR execution requires the isolated Docker worker (CM_OCR_ISOLATED_RUNTIME=true).")
    folder = Path.home() / ".opencodereview"
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    with (folder / ".mortis.lock").open("w") as lock:
        while True:
            if stopped():
                raise ReviewStopped("Review cancelled while waiting for OCR configuration.")
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                time.sleep(0.2)
        path = folder / "config.json"
        if path.exists():
            # Never overwrite an operator's pre-existing OCR configuration.
            try:
                existing = json.loads(path.read_text())
            except (ValueError, OSError) as exc:
                raise ValueError("The review worker contains leftover review files and cannot start safely. Ask the administrator to restart the review worker.") from exc
            if existing.get("_code_mortis_owned") is not True:
                raise ValueError("The review worker already has an OCR configuration and cannot replace it safely. Ask the administrator to check the worker configuration.")
        headers = dict(credentials.get("extra_headers", {}))
        endpoint = urlsplit(connection.get("base_url", ""))
        if endpoint.hostname == "opencode.ai" and endpoint.path.rstrip("/") == "/zen/go/v1":
            # Shared configuration uses OCR's own conversation keys for concurrent invocations.
            headers = {
                key: value
                for key, value in headers.items()
                if key.lower() not in {"user-agent", "x-opencode-session"}
            }
            headers.update({"User-Agent": "code-mortis/0.1.0", "x-opencode-session": (
                "{ocr_session_key}" if session_per_task else str(uuid4())
            )})
        entry = dict(connection["options"])
        entry.pop("prompt_token_ceiling", None)
        if structured_result and connection["protocol"] == "openai-responses":
            # OCR 1.13.1 otherwise forces strict=false, even for our required result schema.
            entry["extra_body"] = {**entry.get("extra_body", {}), "tools.0.strict": True}
        entry.update(
            {
                "protocol": connection["protocol"],
                "model": connection["model"],
                "api_key": credentials.get("api_key", ""),
                "extra_headers": headers,
            }
        )
        if connection["protocol"] != "anthropic-bedrock":
            entry["url"] = safe_endpoint(connection["base_url"])
        data = {
            "_code_mortis_owned": True,
            "provider": "mortis",
            "custom_providers": {"mortis": entry},
            "language": review_settings.get("language", "English"),
            "mcp_servers": {},
        }
        redaction = folder / "redaction.json"
        def update_redaction():
            redaction_values = [credentials.get("api_key", ""), *credentials.get("extra_headers", {}).values(),
                                *credentials.get("redaction_values", [])]
            fd = os.open(redaction, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, "w") as file:
                json.dump(redaction_values, file)
        update_redaction()
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as file:
            json.dump(data, file)
        try:
            yield update_redaction
        finally:
            path.unlink(missing_ok=True)
            redaction.unlink(missing_ok=True)
            for name in ("sessions", "logs"):
                directory = folder / name
                if directory.is_dir() and not directory.is_symlink():
                    shutil.rmtree(directory)


def check_version(directory: Path, env: dict):
    code, output, _ = command([settings().ocr_binary, "version"], directory, env, timeout=10)
    if code or not re.search(rf"\bv{re.escape(settings().ocr_version)}\b", output) or "+cm-context-scope-v2" not in output:
        raise ValueError(f"The review worker needs Open Code Review (OCR) version {settings().ocr_version} with the Code Mortis context guard and file policy. Rebuild and restart the worker image.")


def test_connection(connection: dict, credentials: dict) -> str:
    with tempfile.TemporaryDirectory(prefix="mortis-test-") as directory:
        env = child_environment()
        check_version(Path(directory), env)
        with configuration(connection, credentials, {}):
            code, output, errors = command(
                [settings().ocr_binary, "llm", "test"], Path(directory), env, timeout=250
            )
        if code:
            secrets = (credentials.get("api_key", ""), *credentials.get("extra_headers", {}).values())
            detail = clean_text(errors or output, secrets).strip()
            raise ValueError(
                "OCR connection test failed. "
                + (detail[-1500:] if detail else "Check credentials, protocol, and model.")
            )
        if "✓ Tool-call round trip verified" not in output.splitlines():
            raise ValueError(
                "Connection reached the provider, but tool calls were not verified. Choose a model that supports tools."
            )
    return "Connected. Model completed a tool-call round trip."


def parse_report(raw: str, exit_code: int, secrets: tuple[str, ...] = ()) -> tuple[str, dict, list[dict]]:
    try:
        data = json.loads(raw)
    except ValueError as exc:
        raise ValueError("The review engine returned unreadable JSON. Start a new review; if this repeats, ask the administrator to check the worker logs.") from exc
    if not isinstance(data, dict):
        raise ValueError("The review engine returned an unreadable report. Start a new review; if this repeats, ask the administrator to check the worker logs.")
    manifest = data.get("manifest")
    if not isinstance(manifest, dict) or manifest.get("schema_version") != "ocr.run-manifest/v1":
        raise ValueError("The review engine did not provide a supported record of which files it reviewed. Coverage cannot be confirmed. Ask the administrator to check the OCR version.")
    state = manifest.get("terminal_state")
    if state not in {"complete", "partial", "failed", "skipped"}:
        raise ValueError("The review engine did not return a recognized completion status. The review cannot be marked complete. Start a new review.")
    coverage = manifest.get("coverage", {})
    for key in ("selected", "completed", "reused", "failed", "waived"):
        if not isinstance(coverage.get(key), list):
            raise ValueError("The review engine returned invalid file review results. Coverage cannot be confirmed. Start a new review.")
    identities = {}
    for key, items in coverage.items():
        if key not in {"selected", "completed", "reused", "failed", "waived"}:
            continue
        if any(not isinstance(item, dict) or not isinstance(item.get("item_id"), str) for item in items):
            raise ValueError("The review engine returned file results without valid identifiers. Coverage cannot be confirmed. Start a new review.")
        identities[key] = {item["item_id"] for item in items}
        if len(identities[key]) != len(items):
            raise ValueError("The review engine counted the same file result more than once. Coverage cannot be confirmed. Start a new review.")
    outcomes = [item for key in ("completed", "reused", "failed", "waived") for item in identities[key]]
    if len(set(outcomes)) != len(outcomes) or set(outcomes) != identities["selected"]:
        raise ValueError("The review engine returned missing or overlapping file results. Coverage cannot be confirmed. Start a new review.")
    if state == "complete" and (coverage["failed"] or coverage["waived"]):
        state = "partial" if coverage["completed"] or coverage["reused"] else "failed"
    if exit_code != 0 and state == "complete":
        state = "failed"
    if state == "complete" and any(
        isinstance(warning, dict) and warning.get("type") == "review_limit_reached"
        for warning in data.get("warnings") or []
    ):
        state = "partial"
    raw_comments = data.get("comments")
    if not isinstance(raw_comments, list):
        raise ValueError("OCR returned an invalid findings list.")
    comments = []
    selected_paths = {item.get("path") for item in coverage["selected"]}
    for raw_comment in raw_comments:
        if not isinstance(raw_comment, dict):
            raise ValueError("OCR returned a malformed finding.")
        item = dict(raw_comment)
        item["severity"] = item.get("severity") or "medium"
        try:
            comment = ReviewComment.model_validate(item).model_dump()
        except ValidationError as exc:
            raise ValueError("OCR returned a finding with an invalid path, location, or content.") from exc
        if comment["path"] not in selected_paths:
            raise ValueError("OCR returned a finding outside the selected review scope.")
        comments.append(
            {
                key: clean_text(value, secrets) if isinstance(value, str) else value
                for key, value in comment.items()
            }
        )
    # Keep coverage and metrics, never private reasoning or raw provider requests.
    report = {
        "manifest": manifest,
        "summary": data.get("summary", {}),
        "warnings": data.get("warnings", []),
        "message": data.get("message", ""),
    }
    if isinstance(data.get("excluded_files"), list):
        report["excluded_files"] = data["excluded_files"]
    report = json.loads(clean_text(json.dumps(report), secrets))
    return ("completed" if state == "complete" else state), report, comments


def diff_lines(patch: str) -> set[int]:
    result: set[int] = set()
    line = None
    for row in patch.splitlines():
        match = re.match(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@", row)
        if match:
            line = int(match[1])
        elif line is not None:
            if row.startswith(("+", " ")):
                result.add(line)
                line += 1
            elif row.startswith(("diff --git", "+++", "---")):
                line = None
    return result


def preview_paths(files, count):
    if not isinstance(files, list) or type(count) is not int or len(files) != count:
        raise ValueError("The review engine returned conflicting lists of files to review. Start a new review; if this repeats, ask the administrator to check the worker logs.")
    paths = []
    for item in files:
        path = item.get("path") if isinstance(item, dict) else None
        if not isinstance(path, str) or not path:
            raise ValueError("The review engine selected an invalid file path. The review was stopped because that path cannot be read safely. Ask the administrator to check the worker logs.")
        try:
            ReviewComment.relative_path(path)
        except ValueError:
            raise ValueError("The review engine selected an invalid file path. The review was stopped because that path cannot be read safely. Ask the administrator to check the worker logs.") from None
        paths.append(path)
    if len(set(paths)) != len(paths):
        raise ValueError("The review engine selected the same file more than once. Start a new review; if this repeats, ask the administrator to check the worker logs.")
    return paths


def file_limit_result(paths, maximum):
    if maximum is None or len(paths) <= maximum:
        return None
    message = f"{len(paths)} reviewable files exceeds the maximum of {maximum}."
    entries = [{"item_id": digest(path), "path": path} for path in paths]
    return "skipped", {
        "manifest": {
            "schema_version": "code-mortis.file-limit/v1", "terminal_state": "skipped",
            "coverage": {"selected": entries, "completed": [], "reused": [], "failed": [],
                         "waived": [{**item, "reason": message} for item in entries]},
        },
        "message": message,
        "summary": {"findings": 0, "total_tokens": 0},
    }, []


def review_progress(repo: Path, base: str, head: str, paths: list[str], progress: Callable[[dict], None]):
    # Only OCR's persisted file checkpoints count, never model responses or tool logs.
    folder = Path.home() / ".opencodereview" / "sessions" / str(repo).lstrip("/").replace("/", "-")
    ignored = set()
    offsets, outcomes = {}, {}
    selected = set(paths)
    kinds = {"review_item_done": "completed", "review_item_reused": "reused", "review_item_failed": "failed"}
    last = None
    next_poll = 0.0

    def read(*, force=False):
        nonlocal next_poll, last
        if not force and time.monotonic() < next_poll:
            return
        next_poll = time.monotonic() + 1
        for path in folder.glob("*.jsonl"):
            if path in ignored or path.is_symlink():
                continue
            try:
                with path.open("rb") as stream:
                    if path not in offsets:
                        line = stream.readline(MAX_OUTPUT + 1)
                        if len(line) > MAX_OUTPUT:
                            ignored.add(path)
                            continue
                        if not line.endswith(b"\n"):
                            continue
                        header = json.loads(line)
                        if (not isinstance(header, dict) or header.get("type") != "session_start"
                            or header.get("cwd") != str(repo) or header.get("sessionId") != path.stem
                            or header.get("reviewMode") != "range" or header.get("diffFrom") != base
                            or header.get("diffTo") != head):
                            ignored.add(path)
                            continue
                        offsets[path] = stream.tell()
                    stream.seek(offsets[path])
                    start = stream.tell()
                    while stream.tell() - start < MAX_OUTPUT:
                        line = stream.readline(MAX_OUTPUT + 1)
                        if len(line) > MAX_OUTPUT:
                            ignored.add(path)
                            break
                        if not line.endswith(b"\n"):
                            break  # A concurrent write is not a complete checkpoint yet.
                        offsets[path] = stream.tell()
                        row = json.loads(line)
                        if not isinstance(row, dict) or row.get("sessionId") != path.stem:
                            continue
                        kind = kinds.get(row.get("type")) if isinstance(row.get("type"), str) else None
                        file, fingerprint = row.get("filePath"), row.get("fingerprint")
                        if (kind and isinstance(file, str) and file in selected and isinstance(fingerprint, str)
                            and re.fullmatch(r"[0-9a-f]{64}", fingerprint)):
                            previous = outcomes.get(file)
                            outcomes[file] = "failed" if previous and previous != kind else kind
            except (OSError, ValueError):
                # Progress is best effort; the final report still undergoes full validation.
                ignored.add(path)
        value = {"selected": len(selected), **{kind: sum(item == kind for item in outcomes.values()) for kind in kinds.values()}}
        if value != last:
            progress(value)
            last = value

    def poll(*, force=False):
        try:
            read(force=force)
        except Exception:
            # Filesystem and progress delivery failures must not abort OCR or its final report.
            pass

    poll(force=True)
    return poll


def review(
    full_name: str,
    number: int,
    base_sha: str,
    head_sha: str,
    connection: dict,
    credentials: dict,
    review_settings: dict,
    token: str,
    stopped: Callable[[], bool],
    run_id: str = "",
    progress: Callable[[dict], None] = lambda value: None,
    checkpoint: dict | None = None,
    saved_review: tuple | None = None,
    after_review: Callable | None = None,
    before_analysis: Callable | None = None,
    graph_options: dict | None = None,
) -> tuple[str, dict, list[dict]]:
    from code_mortis.core.shared_execution import require_shared

    if saved_review is None:
        require_shared(connection)
    deadline = time.monotonic() + review_settings["timeout_seconds"] if review_settings["timeout_seconds"] is not None else None
    cancelled = stopped

    def stopped():
        if cancelled():
            return True
        if deadline is not None and time.monotonic() >= deadline:
            raise TimeoutError("The review reached its time limit. Increase Timeout or reduce the review scope, then start a new review.")
        return False

    with git_source.review_snapshot(full_name, number, base_sha, head_sha, token, stopped,
                                git=git, environment=child_environment) as (repo, env, fetch_env, merge_base):
        directory = repo.parent
        def complete(result):
            if after_review:
                after_review(result, repo, env, fetch_env)
            return result

        if saved_review is not None:
            return complete(saved_review)
        if connection.get("kind", "api") != "api":
            from code_mortis.reviews.delegated import review as delegated_review

            return complete(delegated_review(
                repo,
                env,
                base_sha,
                head_sha,
                merge_base,
                connection,
                review_settings,
                run_id,
                stopped,
                progress,
                checkpoint or {},
                before_analysis=before_analysis, credentials={**credentials, "redaction_values": [*credentials.get("redaction_values", []), token]},
                graph_options=graph_options,
            ))
        from code_mortis.reviews.context import prepare_ocr_redaction

        args = [
            settings().ocr_binary,
            "review",
            "--repo",
            str(repo),
            "--from",
            base_sha,
            "--to",
            head_sha,
            "--format",
            "json",
            "--audience",
            "agent",
            "--effort",
            review_settings["effort"],
            "--concurrency",
            str(review_settings["concurrency"]),
            "--max-tokens-budget",
            str(review_settings["token_budget"] or 0),
            "--timeout",
            "0",
        ]
        if review_settings.get("exclusions"):
            args += ["--exclude", review_settings["exclusions"]]
        policy_background = review_policy.background(review_settings)
        if policy_background:
            background = directory / "background.md"
            background.write_text(policy_background)
            args += ["--background-file", str(background)]
        rules = directory / "rules.json"
        rules.write_text(json.dumps(file_policy.review_rules(review_settings)))
        env = {**env, "CM_OCR_REVIEW_RULES": str(rules)}
        args += ["--rule", str(rules)]
        check_version(directory, env)
        limited = None
        credentials.setdefault("redaction_values", []).append(token)
        with configuration(connection, credentials, review_settings, stopped) as update_redaction:
            code, output, _ = command([*args, "--preview"], repo, env, stopped=stopped)
            if code:
                raise ValueError("The review engine could not prepare the files and rules for review. Check file exclusions and review rules, then start a new review.")
            try:
                preview = json.loads(output)
            except ValueError as exc:
                raise ValueError("The review engine could not prepare the files and rules for review. Check file exclusions and review rules, then start a new review.") from exc
            if not isinstance(preview, dict) or not isinstance(preview.get("files"), list):
                raise ValueError("The review engine could not prepare the files and rules for review. Check file exclusions and review rules, then start a new review.")
            if any(not isinstance(item, dict) or type(item.get("will_review")) is not bool for item in preview["files"]):
                raise ValueError("The review engine could not determine which files can be reviewed. Check file exclusions and review rules, then start a new review.")
            paths = preview_paths([item for item in preview["files"] if item["will_review"]], preview.get("reviewable_count"))
            permitted = git_source.review_paths(repo, env, merge_base, head_sha, paths, review_settings, stopped, git=git)
            if set(paths) != permitted:
                raise ValueError("The review engine selected files outside the permitted review scope.")
            limited = file_limit_result(paths, review_settings.get("max_files"))
            if limited and isinstance(preview.get("excluded_files"), list):
                limited[1]["excluded_files"] = preview["excluded_files"]
            if before_analysis:
                before_analysis(limited)
            if limited is None:
                if credentials.get("redaction_rules"):
                    prepare_ocr_redaction(repo, env, merge_base, head_sha, credentials, stopped)
                    update_redaction()
                poll = review_progress(repo, base_sha, head_sha, paths, progress)
                code, output, _ = command(
                    args, repo, env, timeout=None, stopped=stopped, poll=poll
                )
                poll(force=True)
        if limited is not None:
            return complete(limited)
        secrets = (token, credentials.get("api_key", ""), *credentials.get("extra_headers", {}).values())
        status, report, comments = parse_report(output, code, secrets)
        if graph_options:
            report["code_graph"] = {"version": graph_options["version"], "reason": "unsupported_adapter",
                                    "detail": "OCR analysis has no bounded per-file optional-context hook; "
                                              "graph discovery remains available during assessment and verification."}
        deleted = (
            set(
                git(
                    repo,
                    env,
                    "diff",
                    "--name-only",
                    "--diff-filter=D",
                    "-z",
                    f"{merge_base}..{head_sha}",
                    stopped=stopped,
                ).split("\0")
            )
            if comments
            else set()
        )
        for comment in comments:
            if comment["path"] not in paths:
                raise ValueError("OCR returned a finding outside the permitted review scope.")
            ref = merge_base if comment["path"] in deleted else head_sha
            source = git(repo, env, "show", f"{ref}:{comment['path']}", stopped=stopped)
            if comment["end_line"] > len(source.splitlines()):
                raise ValueError("OCR returned a finding outside the reviewed file's lines.")
            patch = git(
                repo,
                env,
                "diff",
                "--no-ext-diff",
                "--no-textconv",
                "--unified=3",
                f"{merge_base}..{head_sha}",
                "--",
                comment["path"],
            )
            comment["source_sha"] = ref
            comment["inline_line"] = comment["end_line"] if comment["end_line"] in diff_lines(patch) else None
        return complete((status, report, comments))
