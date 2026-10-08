import base64
import fcntl
import json
import os
import re
import shutil
import signal
import subprocess
import tempfile
import time
from collections.abc import Callable
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

from pydantic import ValidationError

from .config import settings
from .schemas import ReviewComment
from .security import clean_text, digest, safe_endpoint, safe_repo_name

MAX_OUTPUT = 20_000_000


class ReviewStopped(Exception):
    pass


def child_environment() -> dict[str, str]:
    # Only the selected provider is configured below; application and GitHub secrets stay out.
    allowed = {
        "PATH",
        "LANG",
        "LC_ALL",
        "SSL_CERT_FILE",
        "SSL_CERT_DIR",
        "AWS_REGION",
        "AWS_DEFAULT_REGION",
        "AWS_PROFILE",
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "AWS_SESSION_TOKEN",
        "AWS_SHARED_CREDENTIALS_FILE",
        "AWS_CONFIG_FILE",
        "AWS_WEB_IDENTITY_TOKEN_FILE",
        "AWS_ROLE_ARN",
        "AWS_ROLE_SESSION_NAME",
    }
    env = {key: value for key, value in os.environ.items() if key in allowed}
    # Preserve the actual service account home. Never redirect or modify the operator's home.
    for key in ("HOME", "USERPROFILE"):
        if key in os.environ:
            env[key] = os.environ[key]
    env.update(
        {
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_LFS_SKIP_SMUDGE": "1",
            "NO_COLOR": "1",
        }
    )
    return env


def command(
    args: list[str],
    directory: Path,
    env: dict,
    timeout: int | None = 60,
    stopped: Callable[[], bool] = lambda: False,
) -> tuple[int, str, str]:
    with tempfile.TemporaryFile() as output, tempfile.TemporaryFile() as errors:
        process = subprocess.Popen(
            args,
            cwd=directory,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=output,
            stderr=errors,
            start_new_session=True,
        )
        deadline = time.monotonic() + timeout if timeout is not None else None
        try:
            while process.poll() is None:
                if stopped():
                    raise ReviewStopped("Review cancelled or superseded by a newer commit.")
                if deadline is not None and time.monotonic() > deadline:
                    raise TimeoutError("The operation exceeded its configured time limit.")
                if os.fstat(output.fileno()).st_size + os.fstat(errors.fileno()).st_size > MAX_OUTPUT:
                    raise ValueError("Review output exceeded the 20 MB safety limit.")
                time.sleep(0.2)
            output.seek(0)
            errors.seek(0)
            if os.fstat(output.fileno()).st_size + os.fstat(errors.fileno()).st_size > MAX_OUTPUT:
                raise ValueError("Review output exceeded the 20 MB safety limit.")
            return (
                process.returncode,
                output.read(MAX_OUTPUT).decode("utf-8", "replace"),
                errors.read(MAX_OUTPUT).decode("utf-8", "replace"),
            )
        finally:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait()


def git(directory: Path, env: dict, *args: str, stopped=lambda: False) -> str:
    code, output, _ = command(
        [
            "git",
            "-c",
            "core.hooksPath=/dev/null",
            "-c",
            "protocol.file.allow=never",
            "-c",
            "credential.helper=",
            *args,
        ],
        directory,
        env,
        timeout=180,
        stopped=stopped,
    )
    if code:
        raise ValueError(
            "Git could not fetch or inspect the requested PR commits. Check installation access and retry."
        )
    return output


@contextmanager
def configuration(connection: dict, credentials: dict, review_settings: dict, stopped=lambda: False):
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
                raise ValueError("The worker OCR directory is not clean.") from exc
            if existing.get("_code_mortis_owned") is not True:
                raise ValueError("Refusing to overwrite an existing OCR configuration.")
        headers = dict(credentials.get("extra_headers", {}))
        endpoint = urlsplit(connection.get("base_url", ""))
        if endpoint.hostname == "opencode.ai" and endpoint.path.rstrip("/") == "/zen/go/v1":
            # One session per OCR invocation, stable across its model/tool round trips.
            headers = {
                key: value
                for key, value in headers.items()
                if key.lower() not in {"user-agent", "x-opencode-session"}
            }
            headers.update({"User-Agent": "code-mortis/0.1.0", "x-opencode-session": str(uuid4())})
        entry = dict(connection["options"])
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
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as file:
            json.dump(data, file)
        try:
            yield
        finally:
            path.unlink(missing_ok=True)
            for name in ("sessions", "logs"):
                directory = folder / name
                if directory.is_dir() and not directory.is_symlink():
                    shutil.rmtree(directory)


def check_version(directory: Path, env: dict):
    code, output, _ = command([settings().ocr_binary, "version"], directory, env, timeout=10)
    if code or not re.search(rf"\bv{re.escape(settings().ocr_version)}\b", output):
        raise ValueError(f"The worker requires OCR {settings().ocr_version}.")


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
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("OCR returned a malformed report.")
    manifest = data.get("manifest")
    if not isinstance(manifest, dict) or manifest.get("schema_version") != "ocr.run-manifest/v1":
        raise ValueError("OCR returned an unsupported or missing coverage manifest.")
    state = manifest.get("terminal_state")
    if state not in {"complete", "partial", "failed", "skipped"}:
        raise ValueError("OCR returned an unknown review outcome.")
    coverage = manifest.get("coverage", {})
    for key in ("selected", "completed", "reused", "failed", "waived"):
        if not isinstance(coverage.get(key), list):
            raise ValueError("OCR returned invalid coverage details.")
    identities = {}
    for key, items in coverage.items():
        if key not in {"selected", "completed", "reused", "failed", "waived"}:
            continue
        if any(not isinstance(item, dict) or not isinstance(item.get("item_id"), str) for item in items):
            raise ValueError("OCR returned invalid coverage identities.")
        identities[key] = {item["item_id"] for item in items}
        if len(identities[key]) != len(items):
            raise ValueError("OCR returned duplicate coverage identities.")
    outcomes = [item for key in ("completed", "reused", "failed", "waived") for item in identities[key]]
    if len(set(outcomes)) != len(outcomes) or set(outcomes) != identities["selected"]:
        raise ValueError("OCR coverage does not account for every selected item exactly once.")
    if state == "complete" and (coverage["failed"] or coverage["waived"]):
        state = "partial" if coverage["completed"] or coverage["reused"] else "failed"
    if exit_code != 0 and state == "complete":
        state = "failed"
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
    report = json.loads(clean_text(json.dumps(report), secrets))
    return ("completed" if state == "complete" else state), report, comments


def fingerprint(comment: dict) -> str:
    def text(value):
        return " ".join(value.split()).casefold()

    anchor = text(comment.get("existing_code", "")) or f"{comment['start_line']}:{comment['end_line']}"
    return digest(
        json.dumps(
            [comment["path"], comment.get("category", "other"), anchor, text(comment["content"])],
            ensure_ascii=False,
        )
    )


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
        raise ValueError("OCR returned inconsistent selected-file coverage.")
    paths = []
    for item in files:
        path = item.get("path") if isinstance(item, dict) else None
        if not isinstance(path, str) or not path:
            raise ValueError("OCR returned an invalid selected path.")
        try:
            ReviewComment.relative_path(path)
        except ValueError:
            raise ValueError("OCR returned an invalid selected path.") from None
        paths.append(path)
    if len(set(paths)) != len(paths):
        raise ValueError("OCR returned repeated selected paths.")
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
) -> tuple[str, dict, list[dict]]:
    deadline = time.monotonic() + review_settings["timeout_seconds"] if review_settings["timeout_seconds"] is not None else None
    cancelled = stopped

    def stopped():
        if cancelled():
            return True
        if deadline is not None and time.monotonic() >= deadline:
            raise TimeoutError("The run's time allowance expired.")
        return False

    safe_repo_name(full_name)
    if not all(re.fullmatch(r"[0-9a-f]{40}", sha) for sha in (base_sha, head_sha)):
        raise ValueError("Invalid commit identity.")
    root = settings().work_dir
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    with tempfile.TemporaryDirectory(prefix="review-", dir=root) as temporary:
        directory = Path(temporary)
        repo = directory / "repo"
        repo.mkdir()
        env = child_environment()
        # Fetch credentials stay in this process environment, scoped to GitHub.
        # Temporary workspaces are noexec; no credential helper is written there.
        authorization = base64.b64encode(f"x-access-token:{token}".encode()).decode()
        fetch_env = {
            **env,
            "GIT_CONFIG_COUNT": "1",
            "GIT_CONFIG_KEY_0": "http.https://github.com/.extraHeader",
            "GIT_CONFIG_VALUE_0": f"Authorization: Basic {authorization}",
        }
        git(repo, env, "init", "--quiet")
        git(repo, env, "remote", "add", "origin", f"https://github.com/{full_name}.git")
        fetch_refs = [f"{base_sha}:refs/mortis/base", f"refs/pull/{number}/head:refs/mortis/head"]
        git(repo, fetch_env, "fetch", "--no-tags", "--depth=500", "origin", *fetch_refs, stopped=stopped)
        fetched_head = git(repo, env, "rev-parse", "refs/mortis/head").strip()
        if fetched_head != head_sha:
            raise ReviewStopped("The pull request changed before its commits could be fetched.")
        # Checkout only the trusted base revision. OCR reads proposed changes through git objects.
        git(repo, env, "checkout", "--detach", "--quiet", base_sha, stopped=stopped)
        try:
            merge_base = git(repo, env, "merge-base", base_sha, head_sha).strip()
        except ValueError:
            git(repo, fetch_env, "fetch", "--unshallow", "--no-tags", "origin", *fetch_refs, stopped=stopped)
            merge_base = git(repo, env, "merge-base", base_sha, head_sha).strip()
        # Check that trusted rules cannot lead the resolver outside the checkout through symlinks.
        for path in (repo / ".opencodereview", repo / ".opencodereview" / "rule.json"):
            if path.exists() and not path.resolve().is_relative_to(repo.resolve()):
                raise ValueError("Repository review rules point outside the checkout.")
        def complete(result):
            if after_review:
                after_review(result, repo, env, fetch_env)
            return result

        if saved_review is not None:
            check_version(directory, env)
            return complete(saved_review)
        if connection.get("kind", "api") != "api":
            from .delegated import review as delegated_review

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
            ))
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
        if review_settings.get("background"):
            background = directory / "background.md"
            background.write_text(review_settings["background"])
            args += ["--background-file", str(background)]
        if review_settings.get("rules") is not None:
            rules = directory / "rules.json"
            rules.write_text(json.dumps(review_settings["rules"]))
            args += ["--rule", str(rules)]
        check_version(directory, env)
        limited = None
        with configuration(connection, credentials, review_settings, stopped):
            if review_settings.get("max_files") is not None:
                code, output, _ = command([*args, "--preview"], repo, env, stopped=stopped)
                if code:
                    raise ValueError("OCR could not prepare the review scope.")
                try:
                    preview = json.loads(output)
                except ValueError as exc:
                    raise ValueError("OCR could not prepare the review scope.") from exc
                if not isinstance(preview, dict) or not isinstance(preview.get("files"), list):
                    raise ValueError("OCR could not prepare the review scope.")
                if any(not isinstance(item, dict) or type(item.get("will_review")) is not bool for item in preview["files"]):
                    raise ValueError("OCR returned invalid file eligibility.")
                paths = preview_paths([item for item in preview["files"] if item["will_review"]], preview.get("reviewable_count"))
                limited = file_limit_result(paths, review_settings["max_files"])
            if limited is None:
                code, output, _ = command(
                    args, repo, env, timeout=None, stopped=stopped
                )
        if limited is not None:
            return complete(limited)
        secrets = (token, credentials.get("api_key", ""), *credentials.get("extra_headers", {}).values())
        status, report, comments = parse_report(output, code, secrets)
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
