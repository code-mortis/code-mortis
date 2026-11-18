"""Inert repository snapshots, Trivy results and existing structured AI transports."""

import base64
import json
import re
import sys
import tempfile
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path, PurePosixPath

from mortis_engine.security_scans import engine as native
from pydantic import ValidationError

from code_mortis.core import analysis, file_policy, process
from code_mortis.core import source as git_source
from code_mortis.core.config import settings
from code_mortis.core.db import now
from code_mortis.core.errors import ReviewStopped, RunnerBlocked, engine_stopped
from code_mortis.core.security import digest, safe_repo_name
from code_mortis.core.shared_execution import require_shared
from code_mortis.integrations import llm
from code_mortis.reviews import verification
from code_mortis.schemas.inference import Message, Request
from code_mortis.schemas.security import (
    SecurityAnalysis,
    SecurityAnalysisSelection,
    SecurityAssessment,
    SecurityAssessmentSelection,
    SecurityFix,
    SecurityFixSelection,
)
from code_mortis.security_scans import payload, policy

POLICY = "code-mortis.security/v1"
CITATION_CONTRACT = "source-selection/v1"
LEGACY_TASK_CONTRACT = "security-task/v3"
TASK_CONTRACT = policy.TASK_CONTRACT
SEVERITIES = {"low", "medium", "high", "critical"}
EXCLUDED = {".git", "node_modules", "vendor", "dist", "build", "target", ".venv", "venv", "__pycache__", "coverage"}
CODE_EXTENSIONS = {".py", ".pyi", ".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx", ".go", ".rs", ".rb",
                   ".java", ".kt", ".kts", ".cs", ".c", ".h", ".cpp", ".hpp", ".swift", ".php", ".ex",
                   ".exs", ".scala", ".sh", ".bash", ".vue", ".svelte", ".sql", ".tf", ".yaml", ".yml",
                   ".json", ".toml", ".xml", ".ini", ".properties", ".astro"}
PROTECTED = {".agents", ".claude", ".codex", ".cursor", ".opencodereview", ".aws", ".ssh"}
INSTRUCTION_FILES = {"AGENTS.md", "CLAUDE.md", "GEMINI.md", ".mcp.json", "copilot-instructions.md"}
INSTRUCTIONS = native.INSTRUCTIONS
ASSESSMENT_INSTRUCTIONS = native.ASSESSMENT_INSTRUCTIONS
relative_path = native.relative_path


def secure_digest(value):
    return native.secure_digest(value, settings().encryption_key.encode())


def redact_secrets(text, values, rules=(), stopped=None):
    return native.redact_secrets(text, values, rules, (lambda: engine_stopped(stopped)) if stopped else None,
                                key=settings().encryption_key.encode())


def redacted_context(item, value, *, structural=False):
    if isinstance(value, str):
        try:
            sanitized = item.redact(value)
        except (ValueError, KeyError, TypeError, TimeoutError) as exc:
            raise verification.VerificationOutputError("Security context could not be safely redacted; this task was withheld.",
                {"tokens": 0}, feedback={"kind": "redaction", "reason": "Sanitization failed or exceeded its deadline"}) from exc
        if structural and sanitized != value:
            raise verification.VerificationOutputError("Security context contains credential material in an identifier; this task was withheld.",
                {"tokens": 0}, feedback={"kind": "redaction", "reason": "Credential material in a structural identifier"})
        return sanitized
    if isinstance(value, dict):
        identifiers = {"path", "assigned_path", "head_sha", "original_sha", "id", "source_id", "revision", "finding_id", "available_paths", "required_paths", "paths", "changed_paths", "current_path", "base_sha", "source_sha"}
        return {redacted_context(item, key, structural=True): redacted_context(item, child, structural=key in identifiers)
                for key, child in value.items()}
    if isinstance(value, list):
        return [redacted_context(item, child, structural=structural) for child in value]
    return value


class Snapshot:
    def __init__(self, root, sha, env, stopped):
        self.root, self.sha, self.env, self.stopped = root, sha, env, stopped
        self.git = root / "git"
        self.key = settings().encryption_key.encode()
        self.files = root / "files"
        self.files.mkdir()
        self.inventory, self.excluded, self.problems = {}, [], []
        self.secret_values = []
        self.secret_rules = []
        self.secret_lines = {}
        self.renames = {}

    def read(self, path, sha=None):
        return git_source.read_source(self.git, self.env, sha or self.sha, relative_path(path), self.stopped)

    def redact(self, text):
        return redact_secrets(text, self.secret_values, self.secret_rules, self.stopped)

    def source(self, path, sha=None):
        if any(part in PROTECTED for part in PurePosixPath(path).parts) or PurePosixPath(path).name in INSTRUCTION_FILES:
            raise ValueError("Repository agent instructions and MCP configuration are not supplied to AI.")
        if file_policy.excluded(path):
            raise ValueError("This source is excluded by repository policy.")
        text = self.redact(self.read(path, sha))
        lines = text.splitlines()
        for start, end in self.secret_lines.get(path, []):
            for number in range(start - 1, min(end, len(lines))):
                lines[number] = "[redacted]"
        return "\n".join(lines)

    def populate(self):
        entries = git_source.git(self.git, self.env, "ls-tree", "-r", "-l", "-z", self.sha, stopped=self.stopped)
        total = 0
        for entry in entries.split("\0"):
            if not entry:
                continue
            metadata, path = entry.split("\t", 1)
            relative_path(path)
            mode, kind, blob, size = metadata.split()
            if any(part in EXCLUDED for part in PurePosixPath(path).parts):
                self.excluded.append({"path": path, "reason": "Generated, dependency or cache directory"})
                continue
            if mode not in {"100644", "100755"} or kind != "blob":
                self.excluded.append({"path": path, "reason": "Symlink or submodule is not followed"})
                continue
            if int(size) > 4_000_000:
                self.problems.append({"path": path, "reason": "File exceeds the 4 MB evidence limit"})
                continue
            total += int(size)
            if total > 500_000_000:
                raise ValueError("The repository exceeds the 500 MB security snapshot limit.")
            try:
                source = self.read(path)
            except ValueError:
                self.excluded.append({"path": path, "reason": "Binary or unsupported text encoding"})
                continue
            target = self.files / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(source, encoding="utf-8")
            self.inventory[path] = {"sha": blob, "hash": digest(source), "lines": len(source.splitlines()),
                                    "code": (PurePosixPath(path).suffix.lower() in CODE_EXTENSIONS
                                             or PurePosixPath(path).name.lower() in {"dockerfile", "containerfile"})
                                    and not file_policy.excluded(path)
                                    and not any(p in PROTECTED for p in PurePosixPath(path).parts)
                                    and PurePosixPath(path).name not in INSTRUCTION_FILES}
            name = PurePosixPath(path).name
            if ".generated." in name or ".min." in name or name.endswith((".pb.go", "_pb2.py", "_pb2_grpc.py")) or name in {"package-lock.json", "npm-shrinkwrap.json"}:
                self.inventory[path]["code"] = False
                self.excluded.append({"path": path, "reason": "Generated code or resolved dependency data is checked by Trivy, not AI"})
            elif not self.inventory[path]["code"]:
                self.excluded.append({"path": path, "reason": "No AI analysis for this file type or protected instruction path; Trivy may inspect it as data"})

    def diff(self, original, *, model_context=False):
        scope = []
        if model_context:
            changed = git_source.git(self.git, self.env, "diff", "--no-ext-diff", "--no-textconv", "--no-renames",
                                     "--name-only", "-z", original, self.sha, stopped=self.stopped)
            paths = {relative_path(path) for path in changed.split("\0") if path}
            if not paths:
                return ""
            self.renamed(original, next(iter(paths)))
            for before, after in self.renames[original].items():
                if file_policy.excluded(before) or file_policy.excluded(after):
                    paths.difference_update((before, after))
            paths = sorted(path for path in paths if not file_policy.excluded(path))
            if not paths:
                return ""
            scope = ["--", *(f":(literal){path}" for path in paths)]
        return self.redact(git_source.git(self.git, self.env, "diff", "--no-ext-diff", "--no-textconv", "--find-renames",
                                   original, self.sha, *scope, stopped=self.stopped))

    def renamed(self, original, path):
        if original in self.renames:
            return self.renames[original].get(path, path)
        try:
            names = git_source.git(self.git, self.env, "diff", "--no-ext-diff", "--no-textconv", "--name-status", "-z",
                            "--find-renames", original, self.sha, stopped=self.stopped).split("\0")
        except ValueError:
            self.renames[original] = {}
            raise
        renames = {}
        i = 0
        while i < len(names) - 1:
            kind = names[i]
            if kind.startswith("R"):
                renames[names[i + 1]] = relative_path(names[i + 2])
                i += 3
            else:
                i += 2
        self.renames[original] = renames
        return renames.get(path, path)


@contextmanager
def snapshot(full_name, sha, previous_shas, token, stopped):
    safe_repo_name(full_name)
    if any(not re.fullmatch(r"[0-9a-f]{40}", value) for value in [sha, *previous_shas]):
        raise ValueError("Security scan commit references are invalid.")
    with tempfile.TemporaryDirectory(prefix="mortis-security-", dir=settings().work_dir) as temp:
        item = Snapshot(Path(temp), sha, process.child_environment(), stopped)
        item.git.mkdir()
        git_source.git(item.git, item.env, "init", "--quiet", stopped=stopped)
        authorization = base64.b64encode(f"x-access-token:{token}".encode()).decode()
        fetch_env = {**item.env, "GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "http.https://github.com/.extraHeader",
                     "GIT_CONFIG_VALUE_0": f"Authorization: Basic {authorization}"}
        git_source.git(item.git, fetch_env, "fetch", "--no-tags", "--depth=1", f"https://github.com/{full_name}.git", sha, stopped=stopped)
        for old in set(previous_shas) - {sha}:
            try:
                git_source.git(item.git, fetch_env, "fetch", "--no-tags", "--depth=1", f"https://github.com/{full_name}.git", old, stopped=stopped)
            except ValueError:
                pass  # Unavailable original evidence prevents resolution, not the new scan.
        item.populate()
        yield item


def scanner_environment(root):
    return {key: value for key, value in {**process.child_environment(), "HOME": str(root),
            "TRIVY_DISABLE_TELEMETRY": "true", "TRIVY_SKIP_VERSION_CHECK": "true"}.items()
            if key in {"PATH", "LANG", "LC_ALL", "SSL_CERT_FILE", "SSL_CERT_DIR", "HOME",
                       "TRIVY_DISABLE_TELEMETRY", "TRIVY_SKIP_VERSION_CHECK"}}


def trivy(item, timeout, stopped, *, execution_limits=False):
    deadline = time.monotonic() + timeout if timeout is not None else None

    def remaining(legacy):
        if not execution_limits:
            return legacy
        if deadline is None:
            return None
        value = deadline - time.monotonic()
        if value <= 0:
            raise TimeoutError("Security scan reached its configured time limit during deterministic scanning.")
        return value

    config = settings()
    cache = config.trivy_cache_dir
    cache.mkdir(parents=True, exist_ok=True, mode=0o700)
    env = scanner_environment(item.root)
    policy_overrides = []
    for path in item.inventory:
        target = item.files / path
        if not target.is_file():
            continue
        source = target.read_text()
        inert = re.sub(r"\b(?:trivy|tfsec):ignore", "cmort:ignore", source)
        if inert != source:
            target.write_text(inert)
            item.inventory[path]["scanner_hash"] = digest(inert)
            policy_overrides.append(path)
    code, version, _ = process.command([config.trivy_binary, "--version"], item.root, env, timeout=remaining(10), stopped=stopped)
    if code or not re.search(rf"\b{re.escape(config.trivy_version)}\b", version):
        raise ValueError(f"Security scanning requires Trivy {config.trivy_version}. Rebuild the security worker.")
    trusted = item.root / "trivy-config.yaml"
    trusted.write_text("{}\n")
    ignores = item.root / "trivy-ignore"
    ignores.write_text("")
    secret_config = item.root / "secret-config.yaml"
    secret_config.write_text("skip-patterns: []\n")
    args = [config.trivy_binary, "fs", "--cache-dir", str(cache), "--config", str(trusted),
            "--disable-telemetry", "--skip-version-check", "--ignorefile", str(ignores), "--secret-config", str(secret_config)]

    def fs_arguments(duration):
        if not execution_limits:
            return args
        # Go durations require a positive finite value; process cancellation governs Unlimited.
        nanoseconds = (1 << 63) - 1 if duration is None else max(1, int(duration * 1_000_000_000))
        return [*args, "--timeout", f"{nanoseconds}ns"]

    duration = remaining(min(timeout or 300, 300))
    update_code, _, _ = process.command([*fs_arguments(duration), "--download-db-only"], item.root, env,
                                    timeout=duration, stopped=stopped)
    metadata = {}
    try:
        metadata = json.loads((cache / "db" / "metadata.json").read_text())
        updated = datetime.fromisoformat(metadata["UpdatedAt"].replace("Z", "+00:00"))
        fresh = updated.utcoffset() is not None and 0 <= (now() - updated).total_seconds() <= 86400
    except (ValueError, KeyError, OSError, TypeError):
        fresh = False
    # Secret detection remains available if advisory downloads fail.
    scanners = "vuln,misconfig,secret" if fresh else "misconfig,secret"
    empty = item.root / "check-update"
    empty.mkdir()
    duration = remaining(min(timeout or 300, 300))
    process.command([*fs_arguments(duration), "--scanners", "misconfig", "--skip-db-update", "--offline-scan", str(empty)],
                item.root, env, timeout=duration, stopped=stopped)
    duration = remaining(timeout)
    code, output, errors = process.command([sys.executable, "-m", "code_mortis.security_scans.scanner", *fs_arguments(duration),
                                  "--scanners", scanners, "--format", "json", "--offline-scan", "--skip-check-update",
                                  "--skip-db-update", "--skip-java-db-update", "--list-all-pkgs", "--include-dev-deps", "--include-non-failures",
                                  "--parallel", "2", str(item.files)],
                                 item.root, env, timeout=duration, stopped=stopped)
    if code:
        raise ValueError("Trivy could not complete secret and configuration scanning. No repository source was sent to AI.")
    try:
        data = json.loads(output)
        if not isinstance(data, dict) or data.get("SchemaVersion") != 2 or not isinstance(data.get("Results", []), list):
            raise ValueError()
        result = normalize_trivy(item, data)
    except (ValueError, KeyError, TypeError, AttributeError, IndexError) as exc:
        raise ValueError("Trivy returned an unsupported security report. No repository source was sent to AI.") from exc
    result["metadata"] = {"version": config.trivy_version, "database": metadata,
                          "database_fresh": fresh, "database_update_succeeded": update_code == 0,
                          "scanners": scanners.split(","), "offline": True, "network_isolation": "seccomp socket denial",
                          "secret_policy": "all built-in rules; no path skipping; no repository configuration",
                          "directory_summaries": json.loads(item.redact(json.dumps(result["directory_summaries"]))),
                          "inline_suppressions_disabled": policy_overrides}
    if re.search(r"\b(?:WARN|ERROR|FATAL)\b", errors):
        result["problems"].append("Trivy reported a warning or an analysis error; this scan cannot establish complete coverage.")
    if not fresh:
        result["problems"].append("Vulnerability database is missing or older than 24 hours.")
    bundle = cache / "policy" / "content"
    result["metadata"]["checks_present"] = bundle.exists()
    try:
        result["metadata"]["checks"] = json.loads((cache / "policy" / "metadata.json").read_text())
    except (OSError, ValueError):
        result["metadata"]["checks"] = {"source": "Trivy embedded checks", "version": config.trivy_version}
    return result


target_path = native.target_path
secret_material = native.secret_material
normalize_trivy = native.normalize_trivy
model_schema = native.model_schema


def selected_citations(evidence, context, *, field="evidence"):
    try:
        return native.selected_citations(evidence, context, field=field)
    except native.ResultValidationError as exc:
        raise verification.ResultValidationError(str(exc), exc.feedback) from exc


def validate_citations(evidence, sources, *, field="evidence", recoveries=None):
    try:
        return native.validate_citations(evidence, sources, field=field, recoveries=recoveries)
    except native.ResultValidationError as exc:
        raise verification.ResultValidationError(str(exc), exc.feedback) from exc


def ask(item, connection, credentials, task, context, budget, timeout, stopped, identity,
        *, infer, source_packing=None, select_packing=None,
        output_token_policy=analysis.OUTPUT_TOKEN_POLICY, navigation=None, select_navigation=None,
        record_sources=None):
    require_shared(connection)
    deadline = time.monotonic() + timeout if timeout is not None else None
    denied = payload.excluded_source_paths({"sources": context.get("sources", {}), "assigned_path": context.get("assigned_path")})
    if denied:
        redacted_context(item, denied, structural=True)
        raise verification.VerificationOutputError("Security source is excluded by repository policy; this task was withheld.",
            {"tokens": 0}, feedback={"kind": "context_limit", "reason": "Required source is excluded by repository policy"})
    context = redacted_context(item, {**context, "available_paths": sorted(path for path in item.inventory
        if not file_policy.excluded(path) and redacted_context(item, path) == path)})
    model, selection_model = {"analysis": (SecurityAnalysis, SecurityAnalysisSelection),
        "assessment": (SecurityAssessment, SecurityAssessmentSelection), "verification": (SecurityFix, SecurityFixSelection)}[task]
    schema = model_schema(selection_model)
    source_ids = {}
    history_ids = {}
    if task == "assessment":
        history = context.get("history", [])
        history_ids = {f"history_{index + 1}": finding["id"] for index, finding in enumerate(history)}
        context["history"] = [{**finding, "id": reference} for reference, finding in zip(history_ids, history, strict=True)]
        schema["properties"]["finding_id"]["enum"] = [*history_ids, None]
        schema["properties"]["finding_id"]["description"] = "Select a supplied history entry reference, or null for a new finding."
    instructions = INSTRUCTIONS + {
        "analysis": ("Audit the assigned file and report all evidenced security defects. Context files are supporting evidence. "
                     "Put source_id, start_line and end_line only inside evidence selections, never directly on a finding."),
        "assessment": ASSESSMENT_INSTRUCTIONS,
        "verification": ("Verify the original security defect against original and current source. Absence from a scan is not proof "
                         "of a fix. Fixed requires original_issue_present, an actual source change, original/current citations, "
                         "and an explanation that rules out the original trigger. Check every affected location and caller."),
    }[task]
    current_policy = context.get("task_contract") == TASK_CONTRACT
    if current_policy:
        instructions += " " + policy.instructions(task)

    def supplied_context(feedback=None):
        catalog = {}
        for revision, sources in sorted(context["sources"].items()):
            for path, source in sorted(sources.items()):
                reference = source_ids.setdefault((revision, path), f"source_{len(source_ids) + 1}")
                catalog[reference] = {"revision": revision, "path": path, "line_count": len(source.splitlines())}
        selection_schema = schema["$defs"]["SecurityEvidenceSelection"]["properties"]
        selection_schema["source_id"]["enum"] = list(catalog) or ["unavailable"]
        selection_schema["source_id"]["description"] = "Select a source_id from citation_sources. Empty files cannot be cited."
        for name in ("start_line", "end_line"):
            selection_schema[name]["maximum"] = max(1, max((source["line_count"] for source in catalog.values()), default=0))
        supplied = redacted_context(item, {**context, "citation_sources": catalog,
            **({"validation_feedback": feedback} if feedback else {})})
        if current_policy:
            supplied = payload.compact_evidence(supplied)
        return supplied

    def supporting_sources():
        paths = [path for path in context["available_paths"] if item.inventory[path].get("code")]
        sources = dict(context["sources"]["current"])
        if len(set(paths) | set(sources)) > 16 or not any(path not in sources for path in paths):
            return None
        for path in paths:
            if path not in sources:
                if stopped():
                    raise ReviewStopped("Security scan cancelled.")
                sources[path] = item.source(path)
            if sum(len(text.encode()) for text in sources.values()) > 32768:
                return None
        return sources

    if current_policy:
        from code_mortis.security_scans.context import source_navigation

        def navigation_stopped():
            if deadline is not None and time.monotonic() >= deadline:
                raise verification.VerificationOutputError("Security analysis reached its time limit.", {"tokens": 0}, "skipped")
            return stopped()

        try:
            if navigation is None:
                navigation = source_navigation(item, context["sources"]["current"], context["available_paths"], navigation_stopped)
            navigation = redacted_context(item, navigation)
            if select_navigation is not None:
                navigation = select_navigation(navigation)
            if payload.excluded_source_paths(navigation):
                raise verification.VerificationOutputError("Saved security navigation includes excluded source; retry the security scan.",
                    {"tokens": 0}, feedback={"kind": "context_limit", "reason": "Saved source navigation violates repository policy"})
        except ReviewStopped as exc:
            exc.usage = {"tokens": 0}
            raise
        context["source_navigation"] = navigation

    # Pin the choice before dispatch: peer consumption or transient reads cannot change replay inputs.
    if context.get("task_contract") in {LEGACY_TASK_CONTRACT, TASK_CONTRACT} and task in {"analysis", "assessment"}:
        original, sources, selected = context["sources"], None, False
        if source_packing is not False:
            try:
                sources = supporting_sources()
                if sources is not None:
                    from code_mortis.core.workflow_inference import schema_for_transport
                    from code_mortis.reviews import context as review_context

                    context["sources"] = {**original, "current": sources}
                    packed = review_context.sanitize(supplied_context(), credentials, stopped)
                    request = Request(instructions=review_context.sanitize(instructions, credentials, stopped),
                        messages=[Message(role="user", content=verification.evidence_bundle(packed))],
                        output_schema=schema_for_transport(schema),
                        max_output_tokens=analysis.output_token_limit(output_token_policy), effort="high")
                    required = llm.reservation(connection, request) + (max(1, request.max_output_tokens)
                        if output_token_policy == analysis.WORKFLOW_OUTPUT_POLICY else request.max_output_tokens)
                    selected = source_packing is True or budget is None or required <= budget
            except (ValueError, verification.VerificationOutputError):
                if source_packing is True:
                    raise
            finally:
                context["sources"] = original
                source_ids.clear()
        if source_packing is True and not selected:
            raise verification.VerificationOutputError("The pinned security supporting source is unavailable.", {"tokens": 0})
        if select_packing is not None:
            selected = select_packing(selected)
        if selected:
            sources = sources if sources is not None else supporting_sources()
            if sources is None:
                raise verification.VerificationOutputError("The pinned security supporting source is unavailable.", {"tokens": 0})
            context["sources"] = {**original, "current": sources}

    def validate(raw, supplied):
        try:
            result = selection_model.model_validate(raw, strict=True).model_dump()
        except ValidationError as exc:
            feedback = {"kind": "schema", "errors": exc.errors(include_url=False, include_context=False, include_input=False)}
            raise verification.ResultValidationError(
                "Security result does not match the requested schema. " + json.dumps(feedback), feedback,
            ) from exc
        for index, path in enumerate(result["required_paths"]):
            try:
                relative_path(path)
            except ValueError as exc:
                feedback = {"kind": "schema", "errors": [{"loc": ["required_paths", index], "msg": str(exc)}]}
                raise verification.ResultValidationError(str(exc), feedback) from exc
        evidence = result.get("evidence", [])
        if task == "analysis":
            problems = []
            for index, proposed in enumerate(result["findings"]):
                if proposed["path"] != supplied["assigned_path"]:
                    feedback = {"kind": "schema", "errors": [{"loc": ["findings", index, "path"],
                        "msg": "Security analysis must report findings only in its assigned file.",
                        "expected": supplied["assigned_path"]}]}
                    raise verification.ResultValidationError("Security analysis returned a finding outside its assigned file.", feedback)
                try:
                    proposed["evidence"] = selected_citations(proposed["evidence"], supplied, field=f"findings[{index}].evidence")
                    validate_citations(proposed["evidence"], supplied["sources"], field=f"findings[{index}].evidence")
                except verification.ResultValidationError as exc:
                    problems.extend(exc.feedback["errors"])
                else:
                    if current_policy and not any(citation["revision"] == "current"
                            and citation["path"] == supplied["assigned_path"] for citation in proposed["evidence"]):
                        feedback = {"kind": "schema", "errors": [{"loc": ["findings", index, "evidence"],
                            "msg": "A finding must cite the assigned current file; supporting source alone cannot establish its local cause."}]}
                        raise verification.ResultValidationError("Security finding lacks assigned-file evidence.", feedback)
            if problems:
                feedback = {"kind": "citation_selection", **problems[0], "errors": problems}
                raise verification.ResultValidationError("Security citation selection is invalid. " + json.dumps(feedback), feedback)
        else:
            result["evidence"] = evidence = selected_citations(evidence, supplied)
            revisions = validate_citations(evidence, supplied["sources"])
            if task == "assessment":
                if result["finding_id"] is not None and result["finding_id"] not in history_ids:
                    feedback = {"kind": "history_selection", "field": "finding_id", "allowed_values": [*history_ids, None]}
                    raise verification.ResultValidationError(
                        "Security assessment must select a supplied history entry reference or null. " + json.dumps(feedback), feedback,
                    )
                if result["verdict"] == "confirmed" and (not evidence or "current" not in revisions or result["required_paths"]):
                    feedback = {"kind": "schema", "errors": [{"loc": ["verdict"],
                        "msg": "Confirmed finding needs complete current-source evidence and no required_paths. "
                               "Request necessary source with an uncertain verdict until it is supplied."}]}
                    raise verification.ResultValidationError("Confirmed finding needs complete current-source evidence.", feedback)
            elif result["verdict"] == "fixed" and (
                revisions != {"original", "current"} or not supplied["diff"].strip()
                or not result["original_issue_present"] or not result["change_explanation"].strip() or result["required_paths"]
            ):
                result["verdict"] = "uncertain"
        return model.model_validate(result, strict=True).model_dump()

    used, feedback, corrected = 0, None, False
    evidence_corrected = False

    def evidence_unavailable(paths, reason):
        raise verification.VerificationOutputError(
            "Required security evidence could not be supplied: " + ", ".join(paths) + ". " + reason,
            {"tokens": used}, feedback={"kind": "evidence", "task": task, "required_paths": paths, "reason": reason},
        )

    for attempt in range(4):
        if stopped():
            raise ReviewStopped("Security scan cancelled.")
        remaining = None if budget is None else max(0, budget - used)
        remaining_time = max(0, int(deadline - time.monotonic())) if deadline is not None else None
        if remaining_time is not None and remaining_time <= 0:
            raise verification.VerificationOutputError("Security analysis reached its time limit.", {"tokens": used}, "skipped")
        try:
            request_context = supplied_context(feedback)
            if current_policy and record_sources is not None:
                record_sources(sorted(context["sources"]["current"]))
        except verification.VerificationOutputError as exc:
            exc.usage = {"tokens": used}
            raise
        except ReviewStopped as exc:
            exc.usage = {"tokens": used}
            raise
        try:
            result, usage = infer(request_context, schema=schema, instructions=instructions,
                                  validator=lambda raw, supplied: redacted_context(item, validate(raw, supplied)),
                                  budget=remaining, timeout=remaining_time, round=attempt)
        except ReviewStopped as exc:
            tokens = getattr(exc, "usage", {}).get("tokens")
            exc.usage = {"tokens": used + tokens if type(tokens) is int and tokens >= 0 else None}
            raise
        except (verification.VerificationOutputError, RunnerBlocked) as exc:
            tokens = exc.usage.get("tokens")
            known = type(tokens) is int and tokens >= 0
            used = used + tokens if known else None
            exc.usage = {**exc.usage, "tokens": used}
            if isinstance(exc, RunnerBlocked) and exc.reason == "prompt_limit" and known:
                raise verification.VerificationOutputError("Necessary security context exceeds the connection's per-prompt limit; this task was withheld.",
                    exc.usage, feedback={"kind": "context_limit", "reason": "Necessary source and related history exceed the per-prompt limit"}) from exc
            if (isinstance(exc, verification.VerificationOutputError) and exc.feedback
                    and exc.feedback.get("kind") not in {"context_limit", "redaction"} and known and not corrected and attempt < 3):
                feedback, corrected = exc.feedback, True
                continue
            raise
        tokens = usage.get("tokens")
        if type(tokens) is not int or tokens < 0:
            raise verification.VerificationOutputError("Security token usage is unknown.", {"tokens": None})
        used += tokens
        feedback = None
        if not result["required_paths"]:
            if task == "assessment" and result["finding_id"] is not None:
                result["finding_id"] = history_ids[result["finding_id"]]
            try:
                return redacted_context(item, result), {"tokens": used}
            except verification.VerificationOutputError as exc:
                exc.usage = {"tokens": used}
                raise
        if attempt == 3:
            reason = "Additional source was still required after four evidence passes."
            break
        unavailable = {}
        for path in dict.fromkeys(result["required_paths"]):
            if path in context["sources"]["current"]:
                unavailable[path] = "These complete source files are already supplied, including empty files. Use their source blocks and remove them from required_paths."
                continue
            if path not in context["available_paths"]:
                unavailable[path] = next((entry["reason"] for entry in [*item.excluded, *item.problems]
                               if isinstance(entry, dict) and entry.get("path") == path),
                              "This path is not permitted as supporting source." if path in item.inventory else
                              "This path is not in the prepared snapshot at the scanned commit.")
                continue
            if len(context["sources"]["current"]) >= 100:
                evidence_unavailable([path], "The 100-source-file evidence limit was reached; this is separate from time and token budgets.")
            try:
                context["sources"]["current"][path] = item.source(path)
            except ValueError as exc:
                evidence_unavailable([path], str(exc))
            if task == "verification":
                try:
                    context["sources"].setdefault("original", {})[path] = item.source(path, context["original_sha"])
                except ValueError:
                    pass
        if unavailable:
            paths = list(unavailable)
            reason = "; ".join(f"{path}: {message}" for path, message in unavailable.items())
            if evidence_corrected:
                if task != "analysis" and any(path not in context["available_paths"] for path in paths):
                    break
                evidence_unavailable(paths, reason)
            feedback = {"kind": "evidence", "task": task, "required_paths": paths, "reason": reason}
            evidence_corrected = True
    if task != "analysis":
        result.update(verdict="uncertain", required_paths=[],
                      explanation=("Required security evidence remains unavailable: "
                                   + ", ".join(result["required_paths"]) + ". " + reason)[:12000])
        if task == "assessment":
            result["finding_id"] = None
        try:
            return redacted_context(item, result), {"tokens": used}
        except verification.VerificationOutputError as exc:
            exc.usage = {"tokens": used}
            raise
    evidence_unavailable(result["required_paths"], reason)
