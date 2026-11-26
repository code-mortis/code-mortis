"""Deterministic PR assignments from pinned Git objects; repository code is never executed."""

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from fnmatch import fnmatchcase
from pathlib import PurePosixPath
from typing import ClassVar

from mortis_engine.review import planning as native

from code_mortis.core import file_policy, source
from code_mortis.core.errors import ReviewStopped

LEGACY_VERSION = "mortis-pr-plan/v1"
VERSION = "mortis-pr-plan/v2"
RULES_VERSION = "mortis-rules/v1"
TRUSTED_RULE_PATH = ".opencodereview/rule.json"
MAX_TRUSTED_RULE_BYTES = 512 * 1024
DEFAULT_UNIT_BYTES = 49152
# Language admission is independent of the optional structural graph's parsers.
CODE_SUFFIXES = set((
    "java kt kts scala groovy py pyi js jsx ts tsx mjs cjs c h cpp cc cxx hpp hxx cs vb fs fsi fsx "
    "go rs rb rake gemspec php phtml swift m mm sh bash zsh fish ps1 sql css scss sass less html htm "
    "ftl ftlh ftlx hbs mustache jinja2 j2 pug astro vue ipynb svelte xml yaml yml json toml ini env "
    "gradle cmake r lua pl pm ex exs erl hrl ets json5 dart tf graphql gql prisma jl hcl tfvars bicep "
    "proto nix hs lhs nim nims nimble elm properties po pot jsonnet libsonnet zig thrift capnp ml mli "
    "re rei v sv vh vhd vhdl sol vy rego"
).split())
DEFAULT_DIRS = set((
    "__tests__ __snapshots__ testdata fixtures .ipynb_checkpoints oh_modules bower_components "
    ".pnpm-store .svelte-kit .astro .angular .parcel-cache .docusaurus .bundle .gradle "
    "site-packages .tox Pods Carthage .build obj .dart_tool .terraform .stack-work kitex_gen"
).lower().split())
DEFAULT_PATTERNS = (
    "**/*_test.{go,py,rs,zig}", "**/test_*.py", "**/*.test.{js,jsx,ts,tsx,ets}",
    "**/*.spec.{js,jsx,ts,tsx}", "**/*_spec.rb", "**/*Test.java", "**/*Tests.java", "**/*Test.fs",
    "**/*Test.swift", "**/*Tests.swift", "**/*Spec.hs", "**/*Spec.lhs", "**/*.snap", "**/*.generated.*",
    "**/*.gen.go", "**/*.pb.{go,cc,h}", "**/*.capnp.{h,go,ts}", "**/*_capnp.{rs,py}",
    "**/src/test/java/**", "**/src/test/**/*.{kt,kts}", "**/Tests/**/*.swift", "**/test/**/*.{jl,hs,lhs}",
    "**/tests/**/*.nim", "**/tests/**/*.R", "**/tests/**/*.elm", "**/test/**/*.zig", "**/test/**/*.ml",
    "**/tb_*.{v,sv,vhd,vhdl}", "**/*_tb.{v,sv,vhd,vhdl}", "lib/**/*.sol", "**/*.t.sol",
    "**/test/**/*.sol", "**/tests/**/*.sol", "**/test/**/*.vy", "**/tests/**/*.vy", "**/.pnp.cjs",
    "**/.pnp.loader.mjs", "**/.terraform.lock.hcl", "**/.yarn/{cache,unplugged,releases,sdks,patches}/**",
)
BUILTIN_RULE = (
    "Check changed behavior against its callers, validation and failure paths. Report only concrete "
    "introduced defects with a reachable trigger and observable consequence. Verify authorization, "
    "data integrity, resource bounds and recovery where relevant; support conclusions with source."
)

BUILTIN_CHECKS = (
    ("py pyi", "Trace exceptions, mutable defaults, async boundaries and cleanup through actual Python callers."),
    ("js jsx ts tsx mjs cjs ets", "Check runtime values, promise handling, event lifetimes and state updates; types alone do not validate input."),
    ("go", "Check returned errors, cancellation, deferred cleanup, shared state and channel lifetimes."),
    ("c h cpp cc cxx hpp hxx rs zig", "Check bounds, ownership, object lifetimes, integer conversions and concurrent access."),
    ("java kt kts scala groovy cs vb fs fsi fsx swift m mm dart", "Check nullability, resource ownership, asynchronous work and transaction boundaries."),
    ("sh bash zsh fish ps1", "Check argument quoting, command status, pipelines and destructive filesystem operations."),
    ("sql", "Check query cardinality, NULL behavior, constraints, transaction isolation and parameter handling."),
    ("tf tfvars hcl bicep nix rego", "Check effective permissions, exposed resources, deployment dependencies and upgrade safety."),
    ("json json5 yaml yml toml ini properties xml gradle cmake", "Check configuration consumers, value types, referenced paths, compatibility and effective deployment behavior."),
    ("html htm css scss sass less ftl ftlh ftlx hbs mustache jinja2 j2 pug astro vue svelte", "Check rendered values, escaping, conditional behavior, user interaction and accessible controls."),
    ("sol vy", "Check authority, value transfers, arithmetic, reentrancy and state transitions against reachable transactions."),
    ("v sv vh vhd vhdl", "Check widths, signedness, clock domains, reset behavior and timing-sensitive state transitions."),
    ("md markdown mdx rst txt adoc", "Check factual instructions and examples against the changed implementation and supported configuration."),
)


def system_rule(path):
    suffix = PurePosixPath(path).suffix.lstrip(".").lower()
    specific = next((text for extensions, text in BUILTIN_CHECKS if suffix in extensions.split()), "")
    return "\n\n".join(filter(None, (BUILTIN_RULE, specific)))


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def unit_byte_limit(prompt_token_ceiling):
    # Leave room for framing/escaping; the gateway checks the actual encoded request.
    return (max(1024, min(DEFAULT_UNIT_BYTES, (prompt_token_ceiling - 8192) // 2))
            if prompt_token_ceiling is not None else DEFAULT_UNIT_BYTES)


MAX_RULE_ALTERNATIVES = 256


def _brace_patterns(pattern, *, check=None):
    """Expand bounded alternatives without evaluating shell syntax."""
    pending, result = [pattern], []
    while pending:
        if check is not None:
            check()
        value = pending.pop(0)
        match = re.search(r"\{([^{}]*)\}", value)
        if not match:
            result.append(value)
        else:
            pending.extend(value[:match.start()] + choice + value[match.end():]
                           for choice in match[1].split(","))
        if len(pending) + len(result) > MAX_RULE_ALTERNATIVES:
            raise ValueError("Review rule glob has too many alternatives.")
    return result


def rule_matches(path, pattern, *, check=None):
    """Rule globs are case insensitive; * stays within one path component."""
    parts = path.lower().split("/")
    for expanded in _brace_patterns(pattern.lower(), check=check):
        components = expanded.split("/")

        pending, visited = [(0, 0)], set()
        while pending:
            if check is not None:
                check()
            index, offset = pending.pop()
            if (index, offset) in visited:
                continue
            visited.add((index, offset))
            if offset == len(components):
                if index == len(parts):
                    return True
            elif components[offset] == "**":
                pending.append((index, offset + 1))
                if index < len(parts):
                    pending.append((index + 1, offset))
            elif index < len(parts) and fnmatchcase(parts[index], components[offset]):
                pending.append((index + 1, offset + 1))
    return False


def _entry(repo, env, sha, path, stopped):
    source.repository_path(path)
    output = source.git(repo, env, "--literal-pathspecs", "ls-tree", "-z", "-l", sha, "--", path, stopped=stopped)
    if not output:
        return None
    match = re.fullmatch(r"(\d+) blob ([0-9a-f]{40})\s+(\d+)\t([^\0]+)\0\n?", output)
    if not match or match[4] != path or match[1] not in {"100644", "100755"}:
        raise ValueError("Source is not a regular text file at the pinned revision.")
    return {"blob_sha": match[2], "size": int(match[3])}


def _rule_layer(value, *, check=None):
    if value is None:
        value = {}
    if not isinstance(value, dict):
        raise ValueError("Review rule configuration must be an object.")
    result = {"rules": value.get("rules") or [], "include": value.get("include") or [],
              "exclude": value.get("exclude") or []}
    if (not isinstance(result["rules"], list)
            or any(not isinstance(result[key], list) or any(not isinstance(p, str) for p in result[key])
                   for key in ("include", "exclude"))):
        raise ValueError("Review rules require lists of rules and glob patterns.")
    entries = []
    for entry in result["rules"]:
        if (not isinstance(entry, dict) or not isinstance(entry.get("path", ""), str)
                or not isinstance(entry.get("rule", ""), str)
                or type(entry.get("merge_system_rule", False)) is not bool):
            raise ValueError("Invalid project review rule entry.")
        entries.append({"path": entry.get("path", ""), "rule": entry.get("rule", ""),
                        "merge_system_rule": entry.get("merge_system_rule", False)})
    result["rules"] = entries
    for pattern in [*result["include"], *result["exclude"], *(e["path"] for e in entries)]:
        rule_matches("validation", pattern, check=check)
    return result


def load_rules(repo, env, base_sha, options, stopped):
    """Read inert rules only from the trusted base; never consult a worker's home."""
    return load_legacy_rules(
        base_sha, options,
        lambda path: _entry(repo, env, base_sha, path, stopped),
        lambda path, _entry: source.read_source(repo, env, base_sha, path, stopped, literal=True),
    )


def load_legacy_rules(base_sha, options, entry_at, read_at, *, check=None):
    warnings, layers, inputs = [], [], []
    custom = _rule_layer(options.get("rules"), check=check)
    path = TRUSTED_RULE_PATH
    entry = entry_at(path)
    project = {}
    if entry:
        if entry["size"] > MAX_TRUSTED_RULE_BYTES:
            raise ValueError("Trusted-base review configuration exceeds 512 KiB.")
        content = read_at(path, entry)
        inputs.append({"path": path, **entry})
        project = json.loads(content)
    resolved_bytes = len(json.dumps([custom, project], ensure_ascii=False).encode())
    for layer in (custom, _rule_layer(project, check=check)):
        for item in layer["rules"]:
            text = item["rule"]
            if "\n" not in text and " " not in text and PurePosixPath(text).suffix.lower() in {".md", ".txt", ".markdown"}:
                try:
                    source.repository_path(text)
                    # Dedicated trusted rules are allowed, but cannot read arbitrary secret/instruction paths.
                    policy_path = text.removeprefix(".opencodereview/")
                    policy = file_policy.policy(options)
                    if (file_policy.excluded(policy_path, policy)
                            or any(file_policy.matches(text, pattern) for pattern in policy["exclusions"])
                            or any(rule_matches(text, pattern, check=check) for pattern in custom["exclude"])):
                        raise ValueError("Rule reference is excluded by repository policy.")
                    reference = entry_at(text)
                    if not reference or reference["size"] > MAX_TRUSTED_RULE_BYTES:
                        raise ValueError("Rule reference is missing or exceeds 512 KiB.")
                    resolved_bytes += reference["size"]
                    if resolved_bytes > MAX_TRUSTED_RULE_BYTES:
                        raise ValueError("Resolved review rules exceed 512 KiB.")
                    item["rule"] = read_at(text, reference).rstrip("\n")
                    inputs.append({"path": text, **reference})
                except ValueError:
                    item["rule"] = ""
                    raise ValueError(f"Trusted-base review rule reference {text!r} is unavailable or prohibited.") from None
        layers.append(layer)
        if len(json.dumps(layers, ensure_ascii=False).encode()) > MAX_TRUSTED_RULE_BYTES:
            raise ValueError("Resolved trusted-base review rules exceed 512 KiB.")
    result = {"version": RULES_VERSION, "base_sha": base_sha, "layers": layers, "inputs": inputs,
              "warnings": warnings}
    return {**result, "digest": fingerprint(result)}


def allowed_path(path, options, rules, *, check=None):
    if file_policy.excluded(path, file_policy.policy(options)):
        return False
    # The runtime custom layer always contains mandatory excludes and text includes,
    # preserving its priority over project-local include/exclude configuration.
    custom = rules["layers"][0]
    if any(rule_matches(path, pattern, check=check) for pattern in custom["exclude"]):
        return False
    if any(rule_matches(path, pattern, check=check) for pattern in [*custom["include"], *file_policy.TEXT_CONTEXT]):
        return True
    suffix = PurePosixPath(path).suffix.lstrip(".").lower()
    if suffix and suffix not in CODE_SUFFIXES:
        return False
    return not (any(part.lower() in DEFAULT_DIRS for part in PurePosixPath(path).parts)
                or any(rule_matches(path, pattern, check=check) for pattern in DEFAULT_PATTERNS))


def allowed_context(repo, env, base_sha, head_sha, paths, options, stopped, *, rules=None):
    rules = rules or load_rules(repo, env, base_sha, options, stopped)
    allowed = {path for path in paths if allowed_path(path, options, rules)}
    if not allowed:
        return allowed
    # Trusted rules come from the current base; PR rename scope comes from its merge base.
    merge_base = source.git(repo, env, "merge-base", base_sha, head_sha, stopped=stopped).strip()
    if not re.fullmatch(r"[a-f0-9]{40}", merge_base):
        raise ValueError("Supporting context requires the exact PR merge base.")
    for path, old_path, _status in _changes(repo, env, merge_base, head_sha, stopped):
        if old_path and not all(allowed_path(value, options, rules) for value in (path, old_path)):
            allowed.difference_update((path, old_path))
    return allowed


def _legacy_match(path, rules, *, check):
    for origin, layer in zip(("legacy_settings", "trusted_base"), rules["layers"], strict=True):
        for entry in layer["rules"]:
            if ((entry["rule"] or entry["merge_system_rule"]) and rule_matches(path, entry["path"], check=check)):
                return origin, entry
    return None, None


def _target_matches(path, rule, *, check):
    files = rule.get("files", ())
    globs = rule.get("globs", ())
    legacy_glob = rule["glob"]
    return (not (files or globs or legacy_glob) or path in files
            or bool(legacy_glob and rule_matches(path, legacy_glob, check=check))
            or any(rule_matches(path, glob, check=check) for glob in globs))


def matching_custom_rules(path, review_rules, *, check=None):
    return ((index, rule) for index, rule in enumerate(review_rules)
            if rule["active"] and _target_matches(path, rule, check=check))


def resolve_rules(path, legacy, review_rules=(), *, workspace_count=0, check=None):
    origin, old = _legacy_match(path, legacy, check=check)
    active = list(matching_custom_rules(path, review_rules, check=check))
    instructions, matches = [], []
    if active or old is None or old["merge_system_rule"]:
        instructions.append(system_rule(path))
        matches.append({"name": "Mortis built-in rule", "source": "mortis", "glob": ""})
    if old is not None:
        instructions.append(old["rule"])
        matches.append({"name": "Legacy review rule", "source": origin, "glob": old["path"]})
    for index, rule in active:
        instructions.append(rule["instructions"])
        matches.append({"name": rule["name"], "source": "workspace" if index < workspace_count else "repository",
                        "glob": rule["glob"],
                        **{key: rule[key] for key in ("files", "globs") if rule.get(key)}})
    return "\n\n".join(filter(None, instructions)), matches


def rule_for(path, rules, review_rules=()):
    return resolve_rules(path, rules, review_rules)[0]


@dataclass
class ReviewUnit:
    caller_only: ClassVar[bool] = False
    affected_triggers: ClassVar[tuple] = ()

    key: str
    path: str
    old_path: str | None
    status: str
    source_sha: str
    start_line: int
    end_line: int
    source: str
    patch: str
    rule: str
    input_hash: str

    @property
    def source_only(self):
        return False


@dataclass
class SelectedFile:
    path: str
    old_path: str | None
    status: str
    source_sha: str
    blob_sha: str | None = None
    units: list[ReviewUnit] = field(default_factory=list)
    error: str | None = None


@dataclass
class ReviewPlan:
    # Engine batching reads this attribute. The app persists its own PR scope.
    affected_callers: ClassVar[None] = None

    base_sha: str
    head_sha: str
    merge_base: str
    policy_digest: str
    rules_digest: str
    rules: dict
    selected: list[SelectedFile]
    exclusions: list[dict]
    units: list[ReviewUnit] = field(default_factory=list)
    batches: list[list[ReviewUnit]] = field(default_factory=list)
    limit_reason: str | None = None
    comparison: dict = field(default_factory=dict)

    def snapshot(self):
        return asdict(self)


def _changes(repo, env, merge_base, head_sha, stopped):
    raw = source.git(repo, env, "diff", "--no-ext-diff", "--no-textconv", "--find-renames",
                     "--name-status", "-z", merge_base, head_sha, stopped=stopped)
    fields, changes, index = raw.rstrip("\0\n").split("\0"), [], 0
    while index < len(fields) and fields[index]:
        status = fields[index]
        if not re.fullmatch(r"[AMDT]|[RC]\d{1,3}", status) or index + 1 >= len(fields):
            raise ValueError("Invalid pinned Git change inventory.")
        old, index = source.repository_path(fields[index + 1]), index + 2
        path = old
        if status.startswith(("R", "C")):
            if index >= len(fields):
                raise ValueError("Incomplete pinned rename inventory.")
            path, index = source.repository_path(fields[index]), index + 1
        changes.append((path, old if status.startswith(("R", "C")) else None, status))
    if len({path for path, _, _ in changes}) != len(changes):
        raise ValueError("Duplicate paths in pinned Git change inventory.")
    return sorted(changes)


def _binary_paths(repo, env, merge_base, head_sha, stopped):
    output = source.git(repo, env, "diff", "--no-ext-diff", "--no-textconv", "--no-renames", "--numstat",
                        "-z", merge_base, head_sha, stopped=stopped)
    result = set()
    for row in output.rstrip("\0\n").split("\0"):
        if not row:
            continue
        values = row.split("\t", 2)
        if len(values) != 3:
            raise ValueError("Invalid pinned Git size inventory.")
        path = source.repository_path(values[2])
        if values[0] == "-" or values[1] == "-":
            result.add(path)
    return result


_patch_rows = native._patch_rows
_range_patch = native._range_patch


def split_units(path, old_path, status, sha, text, patch, rule, maximum):
    units = native.split_units(path, old_path, status, sha, text, patch, rule, maximum)
    return [ReviewUnit(**{key: value for key, value in vars(unit).items()
                          if key not in {"source_only", "caller_only", "affected_triggers"}})
            for unit in units]


def plan_review(repo, env, base_sha, head_sha, merge_base, options, stopped, *,
                max_unit_bytes=DEFAULT_UNIT_BYTES, max_batch_bytes=98304, max_source_bytes=20_000_000,
                planner_version=VERSION):
    if planner_version not in (LEGACY_VERSION, VERSION):
        raise ValueError("The saved PR planner version is unsupported.")
    for sha in (base_sha, head_sha, merge_base):
        if not isinstance(sha, str) or not re.fullmatch(r"[a-f0-9]{40}", sha):
            raise ValueError("PR planning requires exact Git revisions.")
    if any(type(limit) is not int or limit < 1024 for limit in (max_unit_bytes, max_batch_bytes, max_source_bytes)):
        raise ValueError("PR context bounds must be whole bytes of at least 1 KiB.")
    if max_batch_bytes < max_unit_bytes:
        raise ValueError("A batch must admit a complete bounded unit.")
    if source.git(repo, env, "merge-base", base_sha, head_sha, stopped=stopped).strip() != merge_base:
        raise ValueError("PR planning cannot substitute a different merge base.")
    rules = load_rules(repo, env, base_sha, options, stopped)
    review_rules = options.get("review_rules", [])
    literal_targets = {path for rule in review_rules if rule["active"] for path in rule.get("files", ())}
    rules_digest = fingerprint({"legacy": rules["digest"], "review_rules": review_rules}) if review_rules else rules["digest"]
    plan = ReviewPlan(base_sha, head_sha, merge_base, fingerprint({"file_policy": file_policy.policy(options), "rules": rules_digest}),
                      rules_digest, rules, [], [])
    binary = _binary_paths(repo, env, merge_base, head_sha, stopped)
    for path, old_path, status in _changes(repo, env, merge_base, head_sha, stopped):
        if not all(allowed_path(value, options, rules) for value in (path, old_path) if value):
            plan.exclusions.append({"path": path, "old_path": old_path, "reason": "excluded"})
        elif path in binary or old_path in binary:
            plan.exclusions.append({"path": path, "old_path": old_path, "reason": "binary"})
        elif status == "D":
            plan.exclusions.append({"path": path, "reason": "deleted", "source_sha": merge_base})
        else:
            plan.selected.append(SelectedFile(path, old_path, status, head_sha))
    maximum = options.get("max_files")
    if maximum is not None and (type(maximum) is not int or maximum < 1):
        raise ValueError("PR max_files must be positive or unlimited.")
    if maximum is not None and len(plan.selected) > maximum:
        plan.limit_reason = f"{len(plan.selected)} reviewable files exceeds the maximum of {maximum}."
        return plan
    used = 0
    for selected in plan.selected:
        if stopped():
            raise ReviewStopped("The review was cancelled or superseded.")
        try:
            entry = _entry(repo, env, head_sha, selected.path, stopped)
            if not entry:
                raise ValueError("Required source is missing at the pinned head.")
            selected.blob_sha = entry["blob_sha"]
            if entry["size"] > max_source_bytes:
                raise ValueError("Required source exceeds the PR source admission limit; splitting cannot bypass it.")
            if used + entry["size"] > max_source_bytes:
                raise ValueError("Required source exceeds the aggregate PR source admission limit.")
            text = source.read_source(repo, env, head_sha, selected.path, stopped, literal=selected.path in literal_targets)
            patch = source.git(repo, env, "--literal-pathspecs", "diff", "--no-ext-diff", "--no-textconv", "--find-renames",
                               "--unified=3", merge_base, head_sha, "--", *dict.fromkeys(filter(None, (selected.old_path, selected.path))), stopped=stopped)
            size = len((text + patch).encode())
            if used + size > max_source_bytes:
                raise ValueError("Required source and diff exceed the aggregate PR source admission limit.")
            units = split_units(selected.path, selected.old_path, selected.status, head_sha,
                                text, patch, rule_for(selected.path, rules, review_rules), max_unit_bytes)
            size = sum(len((unit.source + unit.patch).encode()) for unit in units)
            if used + size > max_source_bytes:
                raise ValueError("Required split context exceeds the aggregate PR source admission limit.")
            used += size
            selected.units = units
            plan.units.extend(units)
        except ValueError as exc:
            selected.error = str(exc)
    batch, size = [], 0
    for unit in plan.units:
        unit_size = len((unit.source + unit.patch + unit.rule).encode()) + 512
        if batch and (size + unit_size > max_batch_bytes or
                      planner_version == VERSION and batch[0].path != unit.path):
            plan.batches.append(batch)
            batch, size = [], 0
        batch.append(unit)
        size += unit_size
    if batch:
        plan.batches.append(batch)
    return plan
