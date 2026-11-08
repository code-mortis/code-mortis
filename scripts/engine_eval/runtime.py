"""Run one synthetic paired-pilot arm through production workflows in a disposable database.

This worker accepts only the neutral runtime manifest and exported Git/context inputs.
It never imports the corpus preparation, truth, or scoring modules. GitHub acquisition
and authorization use a constrained local adapter; every GitHub write is forbidden.
"""

import argparse
import configparser
import copy
import hashlib
import json
import os
import re
import shutil
import stat
import tempfile
import time
from contextlib import ExitStack, contextmanager, nullcontext
from datetime import timedelta
from pathlib import Path
from threading import Lock
from unittest.mock import patch
from urllib.parse import urlsplit
from uuid import uuid4

SCHEMA = "mortis-engine-live-run/v1"
TOKEN = "synthetic-evaluation-github-token"
MAX_PROVIDER_EVENTS = 256


def canonical(value):
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode()


def fingerprint(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def require(condition, message):
    if not condition:
        raise ValueError(message)


def validate_manifest(manifest, case_id, arm, *, live):
    """Fail before importing database/model code or opening provider credentials."""
    require(manifest.get("schema") == SCHEMA, "Unsupported runtime manifest")
    require(
        not set(manifest) & {"truth", "oracle", "defects", "control", "adjudication"},
        "Scorer-only content cannot enter the execution worker",
    )
    require(arm in {"ocr", "mortis"}, "Unsupported engine arm")
    require(
        re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", manifest.get("run_id", "")),
        "Invalid evaluation run identity",
    )
    require(
        manifest["run_id"] == os.environ.get("CM_EVAL_RUN_ID"),
        "Evaluation run identity does not match environment",
    )
    require(
        manifest.get("source_revision") == os.environ.get("CM_EVAL_SOURCE_REVISION"),
        "Worker source revision mismatch",
    )
    for key in ("corpus_digest", "protocol_sha256", "source_revision"):
        length = 40 if key == "source_revision" else 64
        require(
            re.fullmatch(rf"[0-9a-f]{{{length}}}", manifest.get(key, "")),
            f"Invalid {key}",
        )
    require(
        manifest.get("publication") == "disabled_sandbox_adapter_only",
        "Publication must be disabled",
    )
    require(
        manifest.get("graph") == "off",
        "This paired pilot supports only graph-off scope",
    )
    require(manifest.get("repetition") == 1, "The frozen pilot has one paired repetition")
    require(manifest.get("cache") == "cold", "This worker requires a cold source workspace")
    connection = manifest.get("connection", {})
    require(
        connection.get("kind") == "api" and connection.get("owner_id") is None,
        "Only shared API connections are permitted",
    )
    require(
        set(connection)
        <= {
            "id",
            "name",
            "provider",
            "protocol",
            "model",
            "base_url",
            "options",
            "kind",
            "owner_id",
        },
        "Secret or unknown connection fields",
    )
    require(
        all(
            isinstance(connection.get(k), str) and connection[k]
            for k in ("id", "name", "provider", "protocol", "model", "base_url")
        ),
        "Incomplete connection binding",
    )
    require(isinstance(connection.get("options"), dict), "Missing frozen connection options")
    spending = manifest.get("spending", {})
    require(
        spending.get("mode") == "explicit_owner_uncapped",
        "This pilot requires its explicit owner spending selection",
    )
    require(
        all(
            k in spending and spending[k] is None
            for k in (
                "aggregate_usd_cap",
                "aggregate_token_cap",
                "aggregate_request_cap",
            )
        ),
        "Uncapped aggregate policy must be explicit",
    )
    require(
        spending.get("stop_on_unknown_usage") is True,
        "Unknown usage must stop dispatch",
    )
    require(
        manifest.get("price_snapshot") and manifest.get("effective_resources_sha256"),
        "Missing frozen price/resource snapshot",
    )
    require(
        manifest.get("settings") and manifest.get("security_limits"),
        "Missing frozen workflow limits",
    )
    require(
        manifest.get("provider_reasoning_by_workload") == {"pr": "medium", "security": "high"},
        "Missing frozen paired provider reasoning settings",
    )
    cases = [row for row in manifest.get("cases", []) if row.get("case_id") == case_id]
    require(len(cases) == 1, "Case identity is missing or duplicated")
    case = cases[0]
    require(
        set(case)
        <= {
            "case_id",
            "family_id",
            "workload",
            "case_digest",
            "base",
            "head",
            "merge_base_sha",
            "selected_paths",
            "expected_assigned_units",
            "diff_sha256",
            "context_sha256",
            "identity",
            "evaluation_track",
            "scanner_inventory_paths",
        },
        "Runtime case contains unexpected or scorer-only fields",
    )
    require(
        case.get("evaluation_track") == "quality_microcase",
        "Reliability fixtures must use the separate injection harness",
    )
    require(case.get("workload") in {"pr", "security"}, "Unknown case workload")
    require(re.fullmatch(r"(?:pr|sec)-\d{2}", case_id), "Invalid synthetic case identity")
    require(
        len(set(case["selected_paths"])) == len(case["selected_paths"]),
        "Duplicate selected paths",
    )
    require(
        case["expected_assigned_units"] == len(case["selected_paths"]),
        "Invalid frozen coverage denominator",
    )
    if live:
        require(
            manifest.get("live_enabled") is True and manifest.get("authorization") == "owner_selected_pilot",
            "Live pilot has no explicit run authorization",
        )
        require(
            manifest.get("isolation_checks_passed") is True,
            "Fake-provider isolation checks must pass before live inference",
        )
    return case


def validate_environment():
    from sqlalchemy.engine import make_url

    url = make_url(os.environ.get("CM_DATABASE_URL", ""))
    require(
        url.get_backend_name() == "postgresql" and url.database == "mortis_eval",
        "Evaluation requires the separate mortis_eval PostgreSQL database",
    )
    require(
        os.environ.get("CM_EVAL_DISPOSABLE_DATABASE") == "true",
        "Disposable database acknowledgement is required",
    )
    require(
        not any(os.environ.get(k) for k in ("GITHUB_TOKEN", "GH_TOKEN", "CM_GITHUB_TOKEN")),
        "GitHub credentials are forbidden in evaluation workers",
    )


def read_credentials():
    path = Path(os.environ["CM_EVAL_CREDENTIALS_FILE"])
    require(
        path.is_file() and not path.is_symlink(),
        "Credentials must be a private regular file",
    )
    require(
        stat.S_IMODE(path.stat().st_mode) & 0o077 == 0,
        "Credentials file permissions must be private",
    )
    credentials = json.loads(path.read_text())
    require(set(credentials) == {"api_key", "extra_headers"}, "Unexpected credential fields")
    require(
        isinstance(credentials["api_key"], str) and credentials["api_key"],
        "Missing provider credential",
    )
    require(
        isinstance(credentials["extra_headers"], dict)
        and all(isinstance(k, str) and isinstance(v, str) for k, v in credentials["extra_headers"].items()),
        "Invalid provider extra headers",
    )
    return credentials


def provider_options(manifest, case):
    options = copy.deepcopy(manifest["connection"]["options"])
    extra = options.get("extra_body", {})
    require(isinstance(extra, dict), "Provider extra_body must be an object")
    options["extra_body"] = {
        **extra,
        "reasoning.effort": manifest["provider_reasoning_by_workload"][case["workload"]],
    }
    require(
        case["identity"]["model_options_sha256"] == fingerprint(options),
        "Effective provider model options mismatch",
    )
    return options


def checked_case(export, case, options):
    from code_mortis.core import source
    from code_mortis.core.process import child_environment
    from code_mortis.reviews import planning
    from code_mortis.security_scans.engine import Snapshot

    require(
        export.is_dir() and export.name == case["case_id"],
        "Export path must name the frozen case",
    )
    require(
        {p.name for p in export.iterdir()} == {"repo", "context.json", "change.diff"},
        "Only exported source/context/diff may enter the worker",
    )
    require(
        not any(p.is_symlink() for p in export.rglob("*")),
        "Export cannot contain symlinks",
    )
    context = json.loads((export / "context.json").read_text())
    require(
        hashlib.sha256((export / "context.json").read_bytes()).hexdigest() == case["context_sha256"],
        "Context identity mismatch",
    )
    require(
        context["case_id"] == case["case_id"] and context["workload"] == case["workload"],
        "Export case identity mismatch",
    )
    require(
        sorted(context["selection"]["paths"]) == sorted(case["selected_paths"]),
        "Export selected scope mismatch",
    )
    require(
        not context["selection"]["unavailable"],
        "Quality arm cannot inject unavailable evidence",
    )
    root, env = export / "repo", child_environment()
    config = configparser.RawConfigParser(strict=True)
    config.read(root / ".git" / "config")
    require(
        config.sections() == ["core"]
        and set(config["core"])
        <= {
            "repositoryformatversion",
            "filemode",
            "bare",
            "logallrefupdates",
            "ignorecase",
            "precomposeunicode",
        },
        "Export Git configuration may not contain executable settings or external includes",
    )
    require(
        not (root / ".git" / "objects" / "info" / "alternates").exists(),
        "Export Git objects must be self-contained",
    )
    require(
        not (root / ".git" / "hooks").exists() or not any((root / ".git" / "hooks").iterdir()),
        "Export must not contain Git hooks",
    )
    for side in ("base", "head"):
        revision = case[side]
        require(context[side + "_sha"] == revision["commit"], "Context revision mismatch")
        require(
            source.git(root, env, "rev-parse", revision["commit"] + "^{tree}").strip() == revision["tree"],
            "Git tree identity mismatch",
        )
        entries = source.git(root, env, "ls-tree", "-r", "-z", revision["commit"]).strip("\n\0").split("\0")
        actual = {}
        for entry in filter(None, entries):
            meta, path = entry.split("\t", 1)
            mode, kind, blob = meta.split()
            source.repository_path(path)
            require(
                mode in {"100644", "100755"} and kind == "blob",
                "Synthetic source must be regular files",
            )
            body = source.read_source(root, env, revision["commit"], path, lambda: False).encode()
            actual[path] = {
                "git_blob": blob,
                "sha256": hashlib.sha256(body).hexdigest(),
                "bytes": len(body),
            }
        require(actual == revision["blobs"], "Git blob inventory mismatch")
    require(
        source.git(root, env, "merge-base", case["base"]["commit"], case["head"]["commit"]).strip()
        == case["merge_base_sha"],
        "Merge-base mismatch",
    )
    require(
        hashlib.sha256((export / "change.diff").read_bytes()).hexdigest() == case["diff_sha256"],
        "Diff identity mismatch",
    )
    if case["workload"] == "pr":
        plan = planning.plan_review(
            root,
            env,
            case["base"]["commit"],
            case["head"]["commit"],
            case["merge_base_sha"],
            options,
            lambda: False,
        )
        require(
            sorted(item.path for item in plan.selected) == sorted(case["selected_paths"]),
            "Production PR selection does not match frozen scope",
        )
        require(
            not plan.limit_reason and all(not item.error for item in plan.selected),
            "Frozen PR source is incomplete",
        )
    else:
        with tempfile.TemporaryDirectory(prefix="eval-scope-") as temporary:
            item = Snapshot(Path(temporary), case["head"]["commit"], env, lambda: False)
            item.git = root
            item.populate()
            require(
                sorted(path for path, entry in item.inventory.items() if entry["code"])
                == sorted(case["selected_paths"]),
                "Production security selection does not match frozen scope",
            )
            require(
                sorted(item.inventory) == sorted(case["scanner_inventory_paths"]),
                "Scanner inventory mismatch",
            )
    return context


class Measurements:
    """Time the first persisted terminal task output, independently of coverage or truth."""

    def __init__(self):
        self.started = time.monotonic()
        self.phases = {}
        self.first_validated = None
        self.lock = Lock()

    def measure(self, name, function):
        def call(*args, **kwargs):
            started = time.monotonic()
            try:
                return function(*args, **kwargs)
            finally:
                with self.lock:
                    self.phases[name] = self.phases.get(name, 0) + (time.monotonic() - started) * 1000

        return call

    @contextmanager
    def installed(self):
        from code_mortis.core import analysis
        from code_mortis.reviews import assessment, planning, summary, verification
        from code_mortis.security_scans import service

        finish = analysis.finish_task

        def finished(*args, **kwargs):
            result = finish(*args, **kwargs)
            saved = kwargs.get("result") or {}
            output = saved.get("result") or {}
            if (
                result
                and saved.get("kind") in {"final", "validated"}
                and not any(output.get(field) for field in ("required_paths", "required_sources"))
            ):
                with self.lock:
                    if self.first_validated is None:
                        self.first_validated = (time.monotonic() - self.started) * 1000
            return result

        stage = service.stage
        active = {}

        def scan_stage(scan_id, name, status, **kwargs):
            if status == "running" and name not in active:
                active[name] = time.monotonic()
            elif status != "running" and name in active:
                self.phases[name] = (time.monotonic() - active.pop(name)) * 1000
            return stage(scan_id, name, status, **kwargs)

        with ExitStack() as stack:
            for module, name, phase in (
                (planning, "plan_review", "planning"),
                (assessment, "perform", "assessment"),
                (verification, "perform", "verification"),
                (summary, "generate", "summary"),
            ):
                stack.enter_context(patch.object(module, name, self.measure(phase, getattr(module, name))))
            stack.enter_context(patch.object(analysis, "finish_task", finished))
            stack.enter_context(patch.object(service, "stage", scan_stage))
            yield


class SyntheticGitHub:
    """An exact synthetic identity, not a general HTTP mock or network fallback."""

    def __init__(self, case, repo_id, full_name, root):
        self.case, self.repo_id, self.full_name, self.root = (
            case,
            repo_id,
            full_name,
            root,
        )
        self.calls = []

    def authorize(self, db, actor, name, **kwargs):
        from code_mortis.models import Installation, Membership, Repository

        repo = db.get(Repository, self.repo_id)
        require(
            name == self.full_name and actor and actor.enabled and db.get(Membership, actor.id),
            "Synthetic authorization identity mismatch",
        )
        require(
            repo
            and repo.full_name == name
            and repo.active
            and db.get(Installation, repo.installation_id).active,
            "Synthetic repository authorization revoked",
        )
        return {
            "id": self.repo_id,
            "default_branch": "head",
            "permissions": {"pull": True, "push": True},
        }

    def token(self, db, installation_id):
        require(installation_id == self.repo_id, "Synthetic installation mismatch")
        return TOKEN

    def request(self, method, path, token, **kwargs):
        from code_mortis.core import source
        from code_mortis.core.process import child_environment

        require(
            method == "GET" and token == TOKEN and not kwargs,
            "GitHub writes and non-synthetic requests are forbidden",
        )
        self.calls.append({"method": method, "path": path})
        base, head = self.case["base"]["commit"], self.case["head"]["commit"]
        prefix = f"/repos/{self.full_name}"
        if path == prefix:
            return {
                "id": self.repo_id,
                "default_branch": "head",
                "permissions": {"pull": True, "push": True},
            }
        if path == prefix + "/pulls/1":
            return {
                "number": 1,
                "state": "open",
                "head": {"sha": head, "ref": "head"},
                "base": {"sha": base, "repo": {"id": self.repo_id}},
            }
        if path == prefix + "/commits/head":
            return {"sha": head}
        if path == prefix + f"/compare/{base}...{head}":
            names = source.git(
                self.root,
                child_environment(),
                "diff",
                "--name-status",
                "--no-renames",
                base,
                head,
            ).splitlines()
            files = []
            for row in names:
                status, filename = row.split("\t")
                patch_text = source.git(
                    self.root,
                    child_environment(),
                    "--literal-pathspecs",
                    "diff",
                    "--no-ext-diff",
                    "--no-textconv",
                    "--unified=3",
                    base,
                    head,
                    "--",
                    filename,
                )
                lines = patch_text.splitlines()
                start = next(
                    (i for i, line in enumerate(lines) if line.startswith("@@")),
                    len(lines),
                )
                patch_lines = lines[start:]
                files.append(
                    {
                        "filename": filename,
                        "status": {"A": "added", "D": "removed", "M": "modified"}[status],
                        "patch": "\n".join(patch_lines),
                        "additions": sum(line.startswith("+") for line in patch_lines),
                        "deletions": sum(line.startswith("-") for line in patch_lines),
                    }
                )
            return {
                "base_commit": {"sha": base},
                "merge_base_commit": {"sha": base},
                "files": files,
            }
        raise ValueError("Synthetic GitHub request is outside the frozen identity")

    @contextmanager
    def review_snapshot(self, name, number, base_sha, head_sha, token, stopped, **kwargs):
        from code_mortis.core import source
        from code_mortis.core.config import settings
        from code_mortis.core.process import child_environment

        require(
            (name, number, base_sha, head_sha, token)
            == (
                self.full_name,
                1,
                self.case["base"]["commit"],
                self.case["head"]["commit"],
                TOKEN,
            ),
            "Snapshot request identity mismatch",
        )
        require(not stopped(), "Evaluation stopped")
        with tempfile.TemporaryDirectory(prefix="eval-review-", dir=settings().work_dir) as temporary:
            repo = Path(temporary) / "repo"
            shutil.copytree(self.root, repo)
            env = child_environment()
            source.git(repo, env, "checkout", "--detach", "--quiet", base_sha, stopped=stopped)
            yield repo, env, env, self.case["merge_base_sha"]

    @contextmanager
    def security_snapshot(self, name, sha, previous_shas, token, stopped):
        from code_mortis.core.config import settings
        from code_mortis.core.process import child_environment
        from code_mortis.security_scans.engine import Snapshot

        require(
            (name, sha, previous_shas, token) == (self.full_name, self.case["head"]["commit"], [], TOKEN),
            "Security snapshot identity mismatch",
        )
        require(not stopped(), "Evaluation stopped")
        with tempfile.TemporaryDirectory(prefix="eval-security-", dir=settings().work_dir) as temporary:
            item = Snapshot(Path(temporary), sha, child_environment(), stopped)
            shutil.copytree(self.root, item.git)
            item.populate()
            yield item

    @contextmanager
    def installed(self):
        from code_mortis.core import source
        from code_mortis.integrations import comparison, github, ocr
        from code_mortis.security_scans import engine

        comparison.cached.cache_clear()
        command = ocr.command

        def checked_command(args, *positional, **kwargs):
            result = command(args, *positional, **kwargs)
            if len(args) > 1 and args[1] == "review" and "--preview" in args and not result[0]:
                preview = json.loads(result[1])
                paths = ocr.preview_paths(
                    [row for row in preview["files"] if row["will_review"]],
                    preview["reviewable_count"],
                )
                require(
                    sorted(paths) == sorted(self.case["selected_paths"]),
                    "OCR preview differs from the frozen paired scope; inference forbidden",
                )
            return result

        with ExitStack() as stack:
            for target, name, replacement in (
                (github, "request", self.request),
                (github, "authorize_repo", self.authorize),
                (github, "installation_token", self.token),
                (source, "review_snapshot", self.review_snapshot),
                (engine, "snapshot", self.security_snapshot),
            ):
                stack.enter_context(patch.object(target, name, replacement))
            stack.enter_context(patch.object(ocr, "command", checked_command))
            yield
        comparison.cached.cache_clear()


def create_attempt(manifest, case, arm, credentials, options, attempt_id):
    from sqlalchemy import select

    from code_mortis.core.db import Base, now, transaction
    from code_mortis.core.security import seal, unseal
    from code_mortis.models import (
        AppConfig,
        Connection,
        Installation,
        Job,
        Membership,
        PullRequest,
        Repository,
        ReviewRun,
        SecurityScan,
        User,
    )
    from code_mortis.reviews import assessment, context, service
    from code_mortis.security_scans.service import STAGES

    # A fresh database or our exact sentinel only. No schema reset, truncation or migrations here.
    with transaction() as db:
        marker = db.get(AppConfig, 1)
        if marker:
            require(
                marker.github_slug == "eval-" + manifest["run_id"],
                "Disposable database belongs to another run",
            )
        else:
            for table in Base.metadata.sorted_tables:
                if table.name != "alembic_version":
                    require(
                        db.execute(select(table).limit(1)).first() is None,
                        "Evaluation refuses nonempty unmarked database",
                    )
            db.add(
                AppConfig(
                    id=1,
                    github_app_id="synthetic",
                    github_slug="eval-" + manifest["run_id"],
                    client_id="synthetic",
                    client_secret=seal("synthetic"),
                    private_key=seal("synthetic"),
                    webhook_secret=seal("synthetic"),
                )
            )
        require(
            not db.get(Job, attempt_id),
            "Attempt already exists; failed attempts are never overwritten",
        )
        identity = int(hashlib.sha256(attempt_id.encode()).hexdigest()[:14], 16)
        full_name = "mortis-eval/" + case["case_id"] + "-" + attempt_id[:8]
        db.add(User(id=identity, login="eval-" + attempt_id, role="admin"))
        db.add(Membership(github_id=identity, login="eval-" + attempt_id, role="admin"))
        db.add(Installation(id=identity, account="mortis-eval"))
        selected = manifest["connection"]
        connection = db.get(Connection, selected["id"])
        if connection:
            require(
                all(getattr(connection, key) == value for key, value in selected.items()),
                "Frozen shared connection changed",
            )
            require(
                json.loads(unseal(connection.secret)) == credentials, "Frozen provider credential changed"
            )
        else:
            connection = Connection(**selected, secret=seal(json.dumps(credentials)))
            db.add(connection)
        db.flush()
        db.add(
            Repository(
                id=identity,
                installation_id=identity,
                full_name=full_name,
                enabled=True,
                private=False,
                connection_id=connection.id,
                settings=options,
            )
        )
        db.flush()
        config = {
            "analysis_engine": arm,
            "connection": service.connection_snapshot(connection),
            "settings": options,
            "assessment_policy": assessment.POLICY_VERSION,
            "task_contract": context.TASK_CONTRACT,
        }
        if case["workload"] == "pr":
            db.add(
                PullRequest(
                    id=attempt_id,
                    repository_id=identity,
                    number=1,
                    title="Synthetic proposed change",
                    author="synthetic",
                    url="https://example.invalid/evaluation",
                    base_sha=case["base"]["commit"],
                    head_sha=case["head"]["commit"],
                    head_ref="head",
                )
            )
            db.flush()
            db.add(
                ReviewRun(
                    id=attempt_id,
                    pr_id=attempt_id,
                    actor_id=identity,
                    trigger="manual",
                    engine=arm,
                    connection_id=connection.id,
                    dedup_key=attempt_id,
                    base_sha=case["base"]["commit"],
                    head_sha=case["head"]["commit"],
                    config=config,
                    summary={"status": "pending", "tokens": 0},
                    check_enabled=False,
                    check_pending=False,
                )
            )
        else:
            config = {
                "analysis_engine": arm,
                "connection": config["connection"],
                "limits": manifest["security_limits"],
                "policy": __import__("code_mortis.security_scans.engine", fromlist=["POLICY"]).POLICY,
                "rating_policy": "highest-unresolved-severity/v1",
                "trivy_version": "0.76.0",
                "language": options["language"],
            }
            db.add(
                SecurityScan(
                    id=attempt_id,
                    repository_id=identity,
                    actor_id=identity,
                    connection_id=connection.id,
                    request_key=attempt_id,
                    branch="head",
                    head_sha=case["head"]["commit"],
                    config=config,
                    stages={stage: {"status": "pending"} for stage in STAGES},
                    progress={"tasks": {}, "tokens": 0},
                )
            )
        db.flush()
        key = "run_id" if case["workload"] == "pr" else "scan_id"
        db.add(
            Job(
                id=attempt_id,
                kind="review" if key == "run_id" else "security_scan",
                payload={key: attempt_id},
                status="running",
                attempts=1,
                lease_until=now() + timedelta(seconds=manifest["case_deadline_seconds"] + 300),
            )
        )
    return identity, full_name


def row_data(row):
    return {column.name: getattr(row, column.name) for column in row.__table__.columns}


def scored_finding(row, occurrence):
    value = {"finding_id": row.id, **row_data(row)}
    snapshot = occurrence.snapshot
    value["body"] = value.get("content") or "\n".join(
        str(snapshot.get(key, "")) for key in ("explanation", "trigger", "impact", "remediation")
    )
    if snapshot.get("evidence"):
        value["evidence"] = snapshot["evidence"]
    if not value.get("start_line"):
        evidence = value.get("evidence", {})
        if isinstance(evidence, dict):
            value["start_line"] = evidence.get("start_line")
            value["end_line"] = evidence.get("end_line", evidence.get("start_line"))
    return value


def retained_result(case, attempt_id):
    from sqlalchemy import select

    from code_mortis.core.db import transaction
    from code_mortis.models import (
        AnalysisAttempt,
        AnalysisBudget,
        AnalysisPlan,
        AnalysisTask,
        Finding,
        FindingVerification,
        Job,
        Occurrence,
        ReviewCandidate,
        ReviewRun,
        SecurityFinding,
        SecurityOccurrence,
        SecurityScan,
    )
    from code_mortis.reviews.analytics import run_tokens

    with transaction() as db:
        run = db.get(ReviewRun if case["workload"] == "pr" else SecurityScan, attempt_id)
        plans = list(db.scalars(select(AnalysisPlan).where(AnalysisPlan.job_id == attempt_id)))
        tasks = list(db.scalars(select(AnalysisTask).where(AnalysisTask.plan_id.in_([p.id for p in plans]))))
        attempts = list(
            db.scalars(select(AnalysisAttempt).where(AnalysisAttempt.task_id.in_([t.id for t in tasks])))
        )
        budgets = list(
            db.scalars(select(AnalysisBudget).where(AnalysisBudget.plan_id.in_([p.id for p in plans])))
        )
        if case["workload"] == "pr":
            verification_targets = [
                row_data(row)
                for row in db.scalars(
                    select(FindingVerification).where(FindingVerification.run_id == attempt_id)
                )
            ]
            occurrences = list(db.scalars(select(Occurrence).where(Occurrence.run_id == attempt_id)))
            findings = [
                scored_finding(db.get(Finding, occurrence.finding_id), occurrence)
                for occurrence in occurrences
            ]
            candidates = [
                row_data(row)
                for row in db.scalars(select(ReviewCandidate).where(ReviewCandidate.run_id == attempt_id))
            ]
            tokens = run_tokens(db, run)
            coverage = (run.report or {}).get("manifest", {}).get("coverage", {})
            validated = len(coverage.get("completed", []))
            if not coverage:
                validated = len((run.report or {}).get("files", [])) if run.status == "completed" else 0
        else:
            occurrences = list(
                db.scalars(select(SecurityOccurrence).where(SecurityOccurrence.scan_id == attempt_id))
            )
            findings = [
                scored_finding(db.get(SecurityFinding, occurrence.finding_id), occurrence)
                for occurrence in occurrences
            ]
            candidates = []
            verification_targets = None
            tokens = run.progress.get("tokens")
            stage = run.stages.get("analysis", {})
            validated = (
                stage.get("files", stage.get("completed", 0))
                if stage.get("status") == "completed"
                else stage.get("completed", 0)
            )
        return {
            "status": run.status,
            "error": run.error,
            "findings": findings,
            "coverage": {
                "assigned_units": case["expected_assigned_units"],
                "validated_units": validated,
            },
            "usage": {
                "requests": len(attempts) if attempts else None,
                "tokens": tokens,
                "input_tokens": None,
                "output_tokens": None,
                "cache_read_tokens": None,
                "cache_write_tokens": None,
                "reasoning_tokens": None,
                "cost_usd": None,
            },
            "report": run.report,
            "progress": run.progress,
            "stages": run.stages,
            "summary": run.summary if case["workload"] == "pr" else None,
            "candidates": candidates,
            "verification_targets": verification_targets,
            "verification_selection": {
                "observed": case["workload"] == "pr",
                "selected": len(verification_targets) if verification_targets is not None else None,
                "source": "FindingVerification.run_id"
                if case["workload"] == "pr"
                else "security_scan_stages",
            },
            "ledger": {
                "plans": [row_data(p) for p in plans],
                "tasks": [row_data(t) for t in tasks],
                "attempts": [row_data(a) for a in attempts],
                "budgets": [row_data(b) for b in budgets],
            },
            "job": row_data(db.get(Job, attempt_id)),
            "occurrences": [row_data(row) for row in occurrences],
        }


def execute(manifest, case, export, arm, credentials, options, attempt_id):
    from code_mortis.core.config import settings
    from code_mortis.core.db import now, transaction
    from code_mortis.models import Job, ReviewRun, SecurityScan
    from code_mortis.reviews import service
    from code_mortis.security_scans import service as security_service

    measurements = Measurements()
    repo_id, full_name = create_attempt(manifest, case, arm, credentials, options, attempt_id)
    adapter = SyntheticGitHub(case, repo_id, full_name, export / "repo")
    settings().work_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    outcomes = []
    with adapter.installed(), measurements.installed():
        try:
            if case["workload"] == "pr":
                service.perform_review(attempt_id, attempt_id + ":1")
            else:
                security_service.perform(attempt_id, attempt_id + ":1")
        except Exception as exc:  # noqa: BLE001 - retain every failed evaluation attempt
            # Keep provider/source details in production's sanitized durable records only.
            outcomes.append({"kind": "execution_error", "error_type": type(exc).__name__})
            with transaction() as db:
                row = db.get(ReviewRun if case["workload"] == "pr" else SecurityScan, attempt_id)
                row.status, row.finished_at = "failed", now()
                row.error = (
                    "Evaluation workflow raised "
                    + type(exc).__name__
                    + "; inspect retained task diagnostics."
                )
        with transaction() as db:
            job = db.get(Job, attempt_id)
            row = db.get(ReviewRun if case["workload"] == "pr" else SecurityScan, attempt_id)
            job.status = "completed" if row.status in {"completed", "partial", "skipped"} else "failed"
            # Publication/resolve jobs are retained but fenced; no scheduler runs in this worker.
        result = retained_result(case, attempt_id)
    elapsed = (time.monotonic() - measurements.started) * 1000
    result.update(
        {
            "timing_ms": {
                "terminal": elapsed,
                "first_validated": measurements.first_validated,
                **measurements.phases,
            },
            "outcomes": outcomes,
            "github_adapter": {"publication": "forbidden", "requests": adapter.calls},
        }
    )
    return result


def cgroup_metrics():
    root = Path("/sys/fs/cgroup")
    result = {}
    for name in ("cpu.stat", "memory.peak", "memory.current", "memory.events", "pids.current", "pids.events"):
        try:
            raw = (root / name).read_text()[:4096].strip()
            result[name] = (
                int(raw)
                if raw.isdigit()
                else {key: int(value) for key, value in (line.split() for line in raw.splitlines())}
            )
        except (OSError, ValueError):
            result[name] = None
    return result


def captured_execute(manifest, case, export, arm, credentials, options, attempt_id, *, fake_provider=False):
    from capture import capture
    from sqlalchemy import select

    from code_mortis.core.db import now, transaction
    from code_mortis.models import Job

    declared = {**manifest["connection"], "options": provider_options(manifest, case)}
    credentials = copy.deepcopy(credentials)
    endpoint = urlsplit(declared["base_url"])
    if endpoint.hostname == "opencode.ai" and endpoint.path.rstrip("/") == "/zen/go/v1":
        credentials["extra_headers"] = {
            key: value
            for key, value in credentials["extra_headers"].items()
            if key.lower() not in {"user-agent", "x-opencode-session"}
        }
        credentials["extra_headers"].update(
            {"User-Agent": "code-mortis/0.1.0", "x-opencode-session": attempt_id}
        )
    with capture(declared, credentials, allow_insecure_loopback=fake_provider) as recorder:

        def retain_event(event):
            with transaction() as db:
                job = db.scalar(select(Job).where(Job.id == attempt_id).with_for_update())
                require(
                    job and job.status == "running" and job.attempts == 1 and job.lease_until > now(),
                    "Provider capture no longer owns the live evaluation job",
                )
                events = {row["sequence"]: row for row in job.payload.get("provider_events", [])}
                require(
                    event["sequence"] in events or len(events) < MAX_PROVIDER_EVENTS,
                    "Evaluation provider event bound reached; further dispatch is forbidden",
                )
                events[event["sequence"]] = event
                job.payload = {**job.payload, "provider_events": [events[key] for key in sorted(events)]}

        recorder.on_event = retain_event
        effective = copy.deepcopy(manifest)
        # A private per-attempt row prevents credentials/session/history reuse across paired arms.
        effective["connection"] = {**declared, "id": attempt_id, "base_url": recorder.base_url}
        scanner_guard = nullcontext(None)
        if case["workload"] == "security":
            from scanner_preflight import guard_scanner

            require(
                manifest.get("scanner_snapshot", {}).get("schema") == "mortis-engine-scanner-snapshot/v1",
                "Security evaluation requires its frozen scanner snapshot",
            )
            scanner_guard = guard_scanner(manifest["scanner_snapshot"], Path("/scanner-cache"))
        with scanner_guard as scanner_state:
            result = execute(effective, case, export, arm, credentials, options, attempt_id)
        scanner_failed = scanner_state is not None and (
            not scanner_state["validated"] or scanner_state["error"]
        )
        if scanner_state is not None:
            result["scanner_guard"] = scanner_state
        if scanner_failed:
            result["outcomes"].append(
                {"kind": "scanner_preflight_failure", "error_type": scanner_state["error"]}
            )
        events = list(recorder.events)
        result["provider_events"] = events
        result["transport"] = {
            "declared_connection": declared,
            "effective_base_url": recorder.base_url,
            "session_scope": "attempt",
            "blocked_reason": recorder.blocked_reason,
            "event_limit": MAX_PROVIDER_EVENTS,
        }
        keys = (
            "input_tokens",
            "output_tokens",
            "cache_read_tokens",
            "cache_write_tokens",
            "reasoning_tokens",
        )
        result["workflow_reported_tokens"] = result["usage"]["tokens"]
        result["usage"] = {
            "requests": sum(bool(row.get("forwarded")) for row in events),
            "tokens": None,
            "cost_usd": None,
            **{key: None for key in keys},
        }
        for key in (*keys, "total_tokens"):
            values = [row.get(key) for row in events if row.get("forwarded")]
            result["usage"]["tokens" if key == "total_tokens" else key] = (
                sum(values) if all(type(value) is int and value >= 0 for value in values) else None
            )
        if recorder.blocked_reason:
            result["outcomes"].append(
                {"kind": "provider_dispatch_stopped", "reason": recorder.blocked_reason}
            )
            result["stop_pilot"] = True
        else:
            result["stop_pilot"] = bool(scanner_failed) or any(
                row.get("consumption_unknown") for row in events
            )
        result["timing_ms"]["provider_sum"] = sum(
            row.get("elapsed_ms", 0) for row in events if row.get("forwarded")
        )
        result["cgroup"] = cgroup_metrics()
        return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--case", type=Path, required=True)
    parser.add_argument("--arm", choices=["ocr", "mortis"], required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--fake-provider", action="store_true")
    args = parser.parse_args()
    require(str(args.output) == "-" or not args.output.exists(), "Refusing to overwrite a retained attempt")
    manifest = json.loads(args.manifest.read_text())
    require(
        hashlib.sha256(args.manifest.read_bytes()).hexdigest() == os.environ.get("CM_EVAL_MANIFEST_SHA256"),
        "Worker manifest hash mismatch",
    )
    require(
        not (args.dry_run and args.fake_provider),
        "Choose either offline validation or fake-provider execution",
    )
    case = validate_manifest(
        manifest, args.case.name, args.arm, live=not (args.dry_run or args.fake_provider)
    )
    if args.fake_provider:
        endpoint = urlsplit(manifest["connection"]["base_url"])
        require(
            endpoint.hostname in {"127.0.0.1", "::1"}
            and manifest.get("authorization") == "synthetic_provider_check",
            "Fake-provider execution requires an explicit loopback-only synthetic manifest",
        )
    validate_environment()
    from code_mortis.schemas.common import RepoSettings

    options = RepoSettings.model_validate({**manifest["settings"], "analysis_engine": args.arm}).model_dump()
    context = checked_case(args.case, case, options)
    provider_options(manifest, case)
    if case["workload"] == "pr":
        options["background"] = context["context"]
    require(
        case["identity"]["settings_sha256"]
        == fingerprint(
            {
                "settings": {key: value for key, value in options.items() if key != "analysis_engine"},
                "security_limits": manifest["security_limits"],
            }
        ),
        "Effective per-case settings identity mismatch",
    )
    result = {
        "schema": "mortis-engine-eval-attempt/v1",
        "attempt_id": str(uuid4()),
        "run_id": manifest["run_id"],
        "corpus_digest": manifest["corpus_digest"],
        "protocol_sha256": manifest["protocol_sha256"],
        "case_id": case["case_id"],
        "workload": case["workload"],
        "arm": args.arm,
        "repetition": 1,
        "graph": "off",
        "cache": "cold",
        "case_digest": case["case_digest"],
        "identity": case["identity"],
        "manifest_sha256": fingerprint(manifest),
    }
    if args.dry_run:
        result.update({"status": "validated_offline", "external_model_requests": 0})
    else:
        result.update(
            captured_execute(
                manifest,
                case,
                args.case,
                args.arm,
                read_credentials(),
                options,
                result["attempt_id"],
                fake_provider=args.fake_provider,
            )
        )
    if str(args.output) == "-":
        print(json.dumps(result, ensure_ascii=False, default=lambda value: value.isoformat()))
        return
    args.output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with args.output.open("x") as output:
        json.dump(
            result,
            output,
            ensure_ascii=False,
            indent=2,
            default=lambda value: value.isoformat(),
        )
        output.write("\n")
    print(
        json.dumps(
            {
                "attempt_id": result["attempt_id"],
                "case_id": case["case_id"],
                "arm": args.arm,
                "status": result["status"],
            }
        )
    )


if __name__ == "__main__":
    main()
