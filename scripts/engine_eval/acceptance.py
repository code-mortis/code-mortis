"""Prospective, offline M5 acceptance. Never loads or re-scores the visible pilot.

The seal and corpus contain scorer-only truth. Keep them outside worker mounts.
All inputs are evidence supplied by the evaluator; a hash binds evidence, it does
not establish that a claimed independent review or sealed holdout actually exists.
"""

import argparse
import hashlib
import json
import math
import random
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

import score as pilot

ARMS = ("ocr", "mortis")
IDENTITIES = tuple(pilot.PROTOCOL["pair_identity"])
RELIABILITY_CHECKS = {
    "worker_loss", "duplicate_delivery", "cancellation", "revocation", "changed_head",
    "provider_failure", "uncertain_publication", "uncertain_consumption", "coverage",
    "no_duplicate_publication", "no_silent_resolution", "immutable_history", "authorization",
    "secret_handling", "endpoint_restrictions", "no_fallback", "full_workflow_recovery",
    "combined_capacity", "resource_headroom", "production_code_paths", "retained_features",
}
CORPUS_STRATA = {
    "clean", "historical_defects", "critical_defects", "cross_file", "large_changes",
    "other_languages", "documentation_config", "renames_deletions", "excluded_source",
    "missing_evidence", "unsupported_formats", "graph_gaps", "default_parallel_pr",
    "historical_verification", "security_above_concurrency", "overlapping_workloads",
}
PERFORMANCE_BEHAVIORS = {"analysis", "assessment", "historical_verification", "summary", "security_scanner", "cross_file_source", "clean_case", "single_file_source"}
REQUIRED_USAGE_FIELDS = {"requests", "tokens", "input_tokens", "output_tokens"}
CAP_FIELDS = {"aggregate_request_cap": "requests", "aggregate_token_cap": "tokens", "aggregate_usd_cap": "cost_usd"}


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()


def fingerprint(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def require(condition, message):
    if not condition:
        raise ValueError(message)


def sha(value):
    return isinstance(value, str) and len(value) == 64 and all(c in "0123456789abcdef" for c in value)


def positive_int(value):
    return type(value) is int and value > 0


def timestamp(value):
    parsed = datetime.fromisoformat(value)
    require(parsed.tzinfo is not None, "Timestamp must include timezone")
    return parsed


def stratum(item):
    return "/".join(item[field] for field in ("workload", "graph", "cache"))


def validate_protocol(protocol):
    require(protocol.get("schema") == "mortis-engine-acceptance-protocol/v2", "Unsupported acceptance protocol")
    require(protocol.get("repository_scope") == ["wilfredinni/code-mortis"], "Owner-selected representative scope is Code Mortis only")
    require(protocol.get("version") and protocol.get("approved_by"), "Protocol version/owner approval pending")
    experts = protocol.get("independent_reviewer_ids")
    require(isinstance(experts, list) and experts and all(isinstance(item, str) and item.strip() for item in experts)
            and len(set(experts)) == len(experts), "Freeze identities of actual independent acceptance reviewers")
    require(set(protocol.get("allowed_signoff_kinds", [])) and set(protocol["allowed_signoff_kinds"])
            <= {"human", "external_expert", "independent_agent"}, "Freeze actual accepted reviewer kinds")
    materiality = protocol.get("materiality", {})
    require(sha(materiality.get("baseline_calibration_sha256")) and materiality.get("rationale")
            and materiality.get("calibration_kind") == "controlled_ocr_engine_overhead"
            and materiality.get("selected_before_acceptance") is True, "Independent latency materiality calibration pending")
    measurement = protocol.get("performance_measurement", {})
    require(measurement.get("mode") == "deterministic_local_provider"
            and measurement.get("metric") == "queue_to_terminal_ms" and measurement.get("endpoint_scope") == "loopback_only"
            and type(measurement.get("configured_provider_delay_ms")) is int and measurement["configured_provider_delay_ms"] == 0
            and sha(measurement.get("response_fixture_sha256")), "Freeze controlled local response fixture and timing mode; live provider latency is ineligible")
    bootstrap = protocol.get("bootstrap", {})
    require(bootstrap.get("confidence") == .95 and type(bootstrap.get("seed")) is int
            and positive_int(bootstrap.get("resamples")) and bootstrap["resamples"] >= 1000
            and positive_int(bootstrap.get("minimum_families")) and bootstrap["minimum_families"] >= 20,
            "Freeze family bootstrap seed, >=1000 resamples, >=20 families and one-sided 95% bounds")
    for sampling in ("sampling", "performance_sampling"):
        for workload in ("pr", "security"):
            sample = protocol.get(sampling, {}).get(workload, {})
            require(positive_int(sample.get("minimum_cases")) and positive_int(sample.get("repetitions")),
                    "Explicit quality/performance sample/repetition requirements pending")
            require(sample["repetitions"] >= 3 or sample.get("repetition_rationale"),
                    "Fewer than three paired repetitions require a frozen practical rationale")
    comparisons = protocol.get("comparisons", [])
    require(isinstance(comparisons, list) and comparisons, "Required comparisons pending")
    names = set()
    for comparison in comparisons:
        require(comparison.get("workload") in {"pr", "security"}
                and comparison.get("graph") in {"off", "on", "gap"}
                and comparison.get("cache") in {"cold", "warm"}, "Unknown acceptance stratum")
        name = stratum(comparison)
        require(name not in names, "Duplicate acceptance stratum")
        names.add(name)
        for percentile in ("p50", "p95"):
            margin = comparison.get("latency", {}).get(percentile, {})
            require(pilot.number(margin.get("absolute_ms")) and pilot.number(margin.get("relative_fraction"))
                    and margin.get("combine") in {"both", "either"}, "Explicit absolute/relative latency margins and combination pending")
    require(protocol.get("strata_rationale"), "Justify the prospective workload/graph/cache strata")
    for workload in ("pr", "security"):
        declared = [item for item in comparisons if item["workload"] == workload]
        require({item["graph"] for item in declared} == {"off", "on", "gap"}
                and {item["cache"] for item in declared} == {"cold", "warm"},
                "Declare separate PR/security, graph off/on/gap and cold/warm comparisons")
    require(set(protocol.get("required_reliability_checks", [])) >= RELIABILITY_CHECKS,
            "Required reliability/feature/resource checks missing")
    require(set(protocol.get("required_corpus_strata", [])) >= CORPUS_STRATA, "Required representative strata missing")
    require(sha(protocol.get("execution_profile_sha256")), "Frozen resource/budget/deadline profile pending")
    required_usage = protocol.get("required_usage_fields", [])
    require(isinstance(required_usage, list) and set(required_usage) >= REQUIRED_USAGE_FIELDS
            and set(required_usage) <= set(pilot.USAGE_FIELDS) and len(set(required_usage)) == len(required_usage),
            "Freeze required request/input/output/total usage fields")
    require(protocol.get("comparison_usage_fields") == ["requests", "tokens"],
            "Prospective paired consumption compares requests and total tokens")
    spending = protocol.get("spending", {})
    require(spending.get("mode") in {"explicit_owner_uncapped", "finite"}
            and spending.get("stop_on_unknown_usage") is True and all(field in spending for field in CAP_FIELDS),
            "Freeze explicit spending policy without weakening unknown-consumption stops")
    caps = [spending[field] for field in CAP_FIELDS]
    require(all(cap is None or pilot.number(cap) for cap in caps)
            and (all(cap is None for cap in caps) if spending["mode"] == "explicit_owner_uncapped"
                 else any(cap is not None for cap in caps)), "Spending caps conflict with selected policy")
    require(all(spending[field] is None or metric in required_usage for field, metric in CAP_FIELDS.items()),
            "Every finite cap requires a known observable metric; unknown USD cannot enforce a USD cap")
    require(spending["aggregate_usd_cap"] is None,
            "This selected-provider acceptance protocol has no observed USD billing binding; a USD cap is ineligible")


def validate_corpus(corpus, protocol):
    require(corpus.get("schema") == "mortis-engine-acceptance-corpus/v2", "Unsupported acceptance corpus")
    require(corpus.get("repositories") == protocol["repository_scope"], "Heldout corpus differs from selected repository scope")
    require(corpus.get("sealed_heldout") is True and corpus.get("implementer_visible") is False
            and sha(corpus.get("provenance_evidence_sha256")), "Sealed independent heldout provenance required; pilot is ineligible")
    provenance = corpus.get("provenance", {})
    require(fingerprint(provenance) == corpus["provenance_evidence_sha256"]
            and provenance.get("custodian_id") and provenance.get("reviewer_id")
            and isinstance(provenance.get("implementer_ids"), list) and provenance["implementer_ids"]
            and isinstance(provenance.get("truth_author_ids"), list) and provenance["truth_author_ids"]
            and provenance["reviewer_id"] not in provenance["implementer_ids"]
            and provenance["custodian_id"] not in provenance["implementer_ids"]
            and sha(provenance.get("review_packet_sha256")) and sha(provenance.get("source_manifests_sha256")),
            "Independent custody/review packet identities and source-manifest binding required")
    timestamp(provenance["sealed_at"])
    evidence = corpus.get("strata_evidence", {})
    require(all(sha(evidence.get(name)) for name in protocol["required_corpus_strata"]), "Representative corpus evidence missing")
    cases = {}
    for case in corpus.get("cases", []):
        require(case.get("case_id") and case["case_id"] not in cases and case.get("family_id")
                and case.get("repository") in protocol["repository_scope"]
                and case.get("workload") in {"pr", "security"} and sha(case.get("case_digest"))
                and positive_int(case.get("expected_assigned_units")), "Invalid/duplicate frozen case")
        require(case.get("evaluation_track") == "quality_microcase", "Acceptance quality cases must have explicit truth")
        require(isinstance(case.get("source_evidence"), dict) and case["source_evidence"],
                "Independent reviewers need the frozen source evidence or immutable source references")
        truth = case.get("truth", {})
        require(truth.get("expected") in {"clean", "defect"} and isinstance(truth.get("defects"), list)
                and bool(truth["defects"]) == (truth["expected"] == "defect"), "Frozen quality ground truth missing")
        ids = [defect.get("id") for defect in truth["defects"]]
        require(all(ids) and len(set(ids)) == len(ids)
                and all(defect.get("severity") in {"low", "medium", "high", "critical"}
                        for defect in truth["defects"]), "Invalid defect identities/severity")
        require(all(all(defect.get(field) for field in (*pilot.SEMANTIC_FIELDS, "proof"))
                    for defect in truth["defects"]), "Frozen defects need semantic evidence and independent proof")
        cases[case["case_id"]] = case
    reviewed = [{"case_id": case["case_id"], "case_digest": case["case_digest"],
                 "truth_sha256": fingerprint(case["truth"])} for case in corpus["cases"]]
    require(provenance.get("reviewed_cases_sha256") == fingerprint(reviewed), "Independent corpus review does not bind frozen truth/cases")
    for workload in ("pr", "security"):
        selected = [case for case in cases.values() if case["workload"] == workload]
        require(len(selected) >= protocol["sampling"][workload]["minimum_cases"], "Insufficient frozen cases")
        require(len({case["family_id"] for case in selected}) >= protocol["bootstrap"]["minimum_families"],
                "Insufficient independent families")
        require(any(case["truth"]["expected"] == "clean" for case in selected)
                and any(defect["severity"] == "critical" for case in selected for defect in case["truth"]["defects"]),
                "Each workload needs actual clean cases and critical defects, beyond stratum labels")
    return cases


def validate_schedule(schedule, cases, protocol, sampling=None):
    sampling = sampling or protocol["sampling"]
    planned = {}
    for item in schedule:
        identity = (*pilot.key(item), item["arm"])
        require(item.get("case_id") in cases and item.get("arm") in ARMS and identity not in planned,
                "Unknown/duplicate planned attempt")
        require(positive_int(item.get("repetition")) and all(sha(item.get("identity", {}).get(field)) for field in IDENTITIES),
                "Freeze measured pair identities and repetitions")
        planned[identity] = item
    expected = set()
    for case in cases.values():
        for comparison in protocol["comparisons"]:
            if comparison["workload"] != case["workload"]:
                continue
            for repetition in range(1, sampling[case["workload"]]["repetitions"] + 1):
                pair = (case["case_id"], repetition, comparison["graph"], comparison["cache"])
                for arm in ARMS:
                    expected.add((*pair, arm))
                if all((*pair, arm) in planned for arm in ARMS):
                    require(planned[(*pair, "ocr")]["identity"] == planned[(*pair, "mortis")]["identity"],
                            "Planned pair identity mismatch")
    require(set(planned) == expected, "Schedule must retain every frozen paired case/repetition/stratum")
    return planned


def validate_performance_plan(plan, quality_cases, protocol):
    require(isinstance(plan, dict) and plan.get("schema") == "mortis-engine-controlled-performance-plan/v2",
            "Separate visible controlled-performance case plan required")
    require(positive_int(plan.get("analysis_concurrency")), "Freeze controlled security analysis concurrency")
    require(plan.get("response_fixture_sha256") == protocol["performance_measurement"]["response_fixture_sha256"]
            and sha(plan.get("semantic_equivalence_evidence_sha256")) and sha(plan.get("family_provenance_sha256"))
            and set(plan.get("required_behaviors", [])) >= PERFORMANCE_BEHAVIORS,
            "Freeze equivalent nontrivial PR/security fixture behavior and family evidence")
    cases = {case["case_id"]: case for case in plan.get("cases", [])}
    require(cases and len(cases) == len(plan["cases"]) and not cases.keys() & quality_cases.keys(),
            "Performance fixtures must be separate from sealed quality cases")
    for case in cases.values():
        require(case.get("workload") in {"pr", "security"} and case.get("family_id") and sha(case.get("case_digest"))
                and positive_int(case.get("expected_assigned_units"))
                and set(case) == {"case_id", "family_id", "workload", "case_digest", "expected_assigned_units", "expected_work"},
                "Controlled performance cases need truth-free identities and frozen case behavior")
        work = case["expected_work"]
        require(set(work) == {"findings", "assessment_candidates", "historical_verification_targets", "source_files", "security_analysis_tasks"}
                and all(type(value) is int and value >= 0 for value in work.values())
                and work["source_files"] == case["expected_assigned_units"], "Invalid per-case performance behavior counts")
        require(work["security_analysis_tasks"] == (work["source_files"] if case["workload"] == "security" else 0),
                "Security analysis counts must match assigned source files; PR cases cannot claim security tasks")
    for workload in ("pr", "security"):
        relevant = [case for case in cases.values() if case["workload"] == workload]
        require(len(relevant) >= protocol["performance_sampling"][workload]["minimum_cases"]
                and len({case["family_id"] for case in relevant}) >= protocol["bootstrap"]["minimum_families"],
                "Inadequate independent controlled-performance families/cases")
        require(any(case["expected_work"]["assessment_candidates"] > 0 for case in relevant)
                and any(case["expected_work"]["historical_verification_targets"] > 0 for case in relevant)
                and any(case["expected_work"]["source_files"] > 1 for case in relevant)
                and any(case["expected_work"]["source_files"] == 1 for case in relevant)
                and any(case["expected_work"]["findings"] == case["expected_work"]["assessment_candidates"] == 0 for case in relevant),
                "Declared cases must include actual nontrivial assessment/history/cross-file and clean/single-file behavior")
    require(any(case["workload"] == "security" and case["expected_work"]["security_analysis_tasks"] > plan.get("analysis_concurrency", math.inf)
                for case in cases.values()), "Designate security analysis larger than frozen concurrency")
    return cases, validate_schedule(plan["schedule"], cases, protocol, protocol["performance_sampling"])


def freeze(protocol, corpus, schedule, performance_schedule=None, *, frozen_at=None):
    validate_protocol(protocol)
    cases = validate_corpus(corpus, protocol)
    validate_schedule(schedule, cases, protocol)
    validate_performance_plan(performance_schedule, cases, protocol)
    when = frozen_at or datetime.now(UTC).isoformat()
    require(timestamp(corpus["provenance"]["sealed_at"]) <= timestamp(when), "Corpus custody must be sealed before acceptance freeze")
    return {"schema": "mortis-engine-acceptance-seal/v2", "frozen_at": when,
            "protocol": protocol, "corpus": corpus, "schedule": schedule, "performance_schedule": performance_schedule,
            "protocol_sha256": fingerprint(protocol), "corpus_sha256": fingerprint(corpus),
            "schedule_sha256": fingerprint(schedule), "scorer_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "performance_schedule_sha256": fingerprint(performance_schedule),
            "helper_sha256": hashlib.sha256(Path(pilot.__file__).read_bytes()).hexdigest(),
            "authorization": "Scorer-only seal; no provider execution or public adoption authorized"}


def verify_seal(seal):
    require(seal.get("schema") == "mortis-engine-acceptance-seal/v2", "Unsupported acceptance seal")
    for field in ("protocol", "corpus", "schedule", "performance_schedule"):
        require(fingerprint(seal[field]) == seal.get(field + "_sha256"), "Frozen " + field + " drift")
    require(seal.get("scorer_sha256") == hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
            and seal.get("helper_sha256") == hashlib.sha256(Path(pilot.__file__).read_bytes()).hexdigest(),
            "Frozen scorer/helper drift; preserve old scorer version")
    validate_protocol(seal["protocol"])
    cases = validate_corpus(seal["corpus"], seal["protocol"])
    validate_performance_plan(seal["performance_schedule"], cases, seal["protocol"])
    require(timestamp(seal["corpus"]["provenance"]["sealed_at"]) <= timestamp(seal["frozen_at"]),
            "Corpus custody must be sealed before acceptance freeze")
    return cases, validate_schedule(seal["schedule"], cases, seal["protocol"])


def latency_bound(families, margin, quantile, bootstrap):
    """Resample paired families; apply frozen materiality rule inside each draw."""
    def statistic(pairs):
        baseline = pilot.nearest_rank([pair[0] for pair in pairs], quantile)
        candidate = pilot.nearest_rank([pair[1] for pair in pairs], quantile)
        absolute, relative = candidate - baseline, candidate / baseline - 1
        absolute_excess = absolute - margin["absolute_ms"]
        relative_excess = relative * baseline - margin["relative_fraction"] * baseline
        excess = (max if margin["combine"] == "both" else min)(absolute_excess, relative_excess)
        require(all(math.isfinite(value) for value in (absolute, relative, excess)), "Latency arithmetic overflow")
        return absolute, relative, excess

    names = sorted(families)
    pairs = [pair for family in names for pair in families[family]]
    if not pairs:
        return {"status": "inconclusive", "reasons": ["no_complete_pairs"]}
    point = statistic(pairs)
    rng = random.Random(bootstrap["seed"])
    samples = [statistic([pair for family in rng.choices(names, k=len(names)) for pair in families[family]])
               for _ in range(bootstrap["resamples"])]
    excesses = [sample[2] for sample in samples]
    upper = pilot.nearest_rank(excesses, .95)
    reasons = []
    if len(names) < bootstrap["minimum_families"]:
        reasons.append("inadequate_independent_families")
    if len(set(excesses)) <= 1:
        reasons.append("degenerate_bootstrap")
    return {"status": "inconclusive" if reasons else "pass" if upper <= 0 else "not_established",
            "reasons": reasons, "independent_families": len(names), "paired_observations": len(pairs),
            "absolute_ms": point[0], "relative_fraction": point[1], "margin_excess_ms": point[2],
            "one_sided_95_upper_excess_ms": upper, "margin": margin,
            "stretch_point_met": point[1] <= (-.5 if quantile == .5 else -.3)}


def quality_bounds(rows, bootstrap):
    families = sorted({row["family_id"] for row in rows})
    grouped = {family: [row for row in rows if row["family_id"] == family] for family in families}

    def differences(sample):
        observed = {arm: pilot.metrics([row for row in sample if row["arm"] == arm]) for arm in ARMS}
        return {field: observed["mortis"][field] - observed["ocr"][field]
                if all(observed[arm][field] is not None for arm in ARMS) else None
                for field in ("precision", "recall")}

    point = differences(rows)
    rng = random.Random(bootstrap["seed"])
    samples = [differences([row for family in rng.choices(families, k=len(families)) for row in grouped[family]])
               for _ in range(bootstrap["resamples"])] if families else []
    result = {}
    for field in ("precision", "recall"):
        values = [sample[field] for sample in samples if sample[field] is not None]
        reasons = []
        if len(families) < bootstrap["minimum_families"]:
            reasons.append("inadequate_independent_families")
        if len(values) != bootstrap["resamples"] or point[field] is None:
            reasons.append("undefined_or_no_event_resamples")
        if len(set(values)) <= 1:
            reasons.append("degenerate_bootstrap")
        lower = pilot.nearest_rank(values, .05)
        result[field] = {"mortis_minus_ocr": point[field], "one_sided_95_lower": lower,
                         "status": "inconclusive" if reasons else "pass" if point[field] >= 0 and lower >= 0 else "not_established",
                         "reasons": reasons, "independent_families": len(families)}
    return result


def blind(results, corpus):
    """New packet schema; reuse the unchanged semantic-matching decision contract."""
    cases = {case["case_id"]: case for case in corpus["cases"]}
    items = []
    for attempt in results["attempts"]:
        case = cases[attempt["case_id"]]
        for finding in attempt["findings"]:
            presentation = {field: pilot.blind_evidence(finding[field]) for field in
                            ("title", "body", "description", "severity", "path", "start_line", "end_line", "evidence", "citations", "rule_id")
                            if field in finding}
            items.append({"blind_id": pilot.blind_id(attempt, finding), "case_id": case["case_id"],
                          "workload": case["workload"], "finding": presentation,
                          "defects": case["truth"]["defects"], "source_evidence": case.get("source_evidence", {})})
    random.Random(0).shuffle(items)
    return {"schema": "mortis-engine-acceptance-blinded-findings/v2", "results_sha256": fingerprint(results),
            "corpus_sha256": fingerprint(corpus), "items": items}


def controlled_performance(seal, results):
    if results is None:
        return {}, {"blockers": ["controlled_performance_results_pending"], "attempt_inventory": []}
    cases, schedule = validate_performance_plan(seal["performance_schedule"], {}, seal["protocol"])
    require(results.get("schema") == "mortis-engine-controlled-performance-results/v2"
            and results.get("seal_sha256") == fingerprint(seal)
            and results.get("measurement") == seal["protocol"]["performance_measurement"],
            "Live quality/provider-network timings cannot enter controlled engine performance")
    require(timestamp(results["started_at"]) >= timestamp(seal["frozen_at"]), "Controlled run predates freeze")
    groups, seen, seen_ids, blockers, inventory = defaultdict(lambda: defaultdict(list)), set(), set(), set(), []
    pairs = defaultdict(dict)
    for attempt in results["attempts"]:
        identity = (*pilot.key(attempt), attempt["arm"])
        require(identity in schedule and identity not in seen and isinstance(attempt.get("attempt_id"), str)
                and attempt["attempt_id"].strip() and attempt["attempt_id"] not in seen_ids,
                "Unknown/duplicate controlled-performance attempt")
        seen.add(identity)
        seen_ids.add(attempt.get("attempt_id"))
        case = cases[attempt["case_id"]]
        require(attempt.get("case_digest") == case["case_digest"] and attempt.get("identity") == schedule[identity]["identity"],
                "Controlled fixture/pair identity drift")
        gaps = pilot.required_work_gaps(attempt, case["workload"], results.get("scanner_snapshot", {}).get("sha256"))
        work = attempt.get("nontrivial_work_evidence", {})
        expected_work = case["expected_work"]
        if not (sha(work.get("artifact_sha256"))
                and all(type(work.get(field)) is int and work[field] == expected for field, expected in expected_work.items())
                and isinstance(attempt.get("findings"), list) and len(attempt["findings"]) == expected_work["findings"]
                and isinstance(work.get("source_paths"), list)
                and len(set(work["source_paths"])) == expected_work["source_files"]):
            gaps.append("missing_or_mismatched_declared_work_evidence")
        if case["workload"] == "pr" and (work.get("assessment_candidates") != len(attempt.get("candidates", []))
                                          or work.get("historical_verification_targets") != len(attempt.get("verification_targets", []))):
            gaps.append("nontrivial_work_counters_do_not_match_retained_outcomes")
        if case["workload"] == "security":
            # OCR and Mortis share these validated workflow checkpoints; only Mortis has a gateway ledger.
            tasks = attempt.get("progress", {}).get("tasks", {})
            grouped = {phase: {key.split(":", 1)[1]: task for key, task in tasks.items() if key.startswith(phase + ":")}
                       for phase in ("analysis", "assessment", "verification")} if isinstance(tasks, dict) else {}
            records = [task for phase in grouped.values() for task in phase.values()]
            if (set(grouped.get("analysis", {})) != set(work.get("source_paths", []))
                    or len(grouped.get("analysis", {})) != expected_work["security_analysis_tasks"]
                    or any(not isinstance(task, dict) or task.get("status") != "completed"
                           or not sha(task.get("input_hash")) or not isinstance(task.get("result"), dict)
                           for task in records)):
                gaps.append("security_analysis_counts_do_not_match_retained_tasks")
            stages = attempt.get("stages", {})
            analysis, assessment, verification = (stages.get(phase, {}) for phase in ("analysis", "assessment", "verification"))
            observed = [analysis.get("files"), analysis.get("completed"), analysis.get("failed"),
                        assessment.get("total"), assessment.get("completed"), assessment.get("failed"),
                        verification.get("total"), verification.get("completed")]
            expected = [expected_work["security_analysis_tasks"], expected_work["security_analysis_tasks"], 0,
                        expected_work["assessment_candidates"], expected_work["assessment_candidates"], 0,
                        expected_work["historical_verification_targets"], expected_work["historical_verification_targets"]]
            if (len(grouped.get("assessment", {})) != expected_work["assessment_candidates"]
                    or len(grouped.get("verification", {})) > expected_work["historical_verification_targets"]
                    or observed != expected or any(type(value) is not int for value in observed)):
                gaps.append("security_work_counters_do_not_match_retained_outcomes")
        coverage = attempt.get("coverage", {})
        complete = (attempt.get("status") in {"complete", "completed"}
                    and type(coverage.get("assigned_units")) is int and coverage["assigned_units"] == case["expected_assigned_units"]
                    and type(coverage.get("validated_units")) is int and coverage["validated_units"] == coverage["assigned_units"]
                    and not gaps and attempt.get("timing_origin") == "queued"
                    and pilot.number(attempt.get("timing_ms", {}).get("terminal")) and attempt["timing_ms"]["terminal"] > 0)
        resource = attempt.get("resource_evidence", {})
        measured = attempt.get("performance_evidence", {})
        transport = attempt.get("transport", {})
        upstream = urlsplit(transport.get("declared_connection", {}).get("base_url", ""))
        valid = (resource.get("profile_sha256") == seal["protocol"]["execution_profile_sha256"]
                 and resource.get("within_limits") is True and sha(resource.get("artifact_sha256"))
                 and measured.get("measurement") == seal["protocol"]["performance_measurement"]
                 and sha(measured.get("artifact_sha256"))
                 and transport.get("provider_mode") == "controlled_synthetic_http"
                 and upstream.scheme == "http" and upstream.hostname in {"127.0.0.1", "localhost", "::1"}
                 and not (upstream.username or upstream.password or upstream.query or upstream.fragment))
        if not complete or not valid:
            blockers.add("controlled_required_work_timing_or_resource_evidence_incomplete")
        inventory.append({"attempt_id": attempt.get("attempt_id"), "status": attempt.get("status"),
                          "coverage": coverage, "timing_ms": attempt.get("timing_ms", {}),
                          "resource_evidence": resource, "performance_evidence": measured,
                          "nontrivial_work_evidence": work, "required_work_gaps": gaps})
        if complete and valid:
            pairs[pilot.key(attempt)][attempt["arm"]] = attempt
    if seen != set(schedule):
        blockers.add("controlled_planned_attempts_missing")
    for key, arms in pairs.items():
        if set(arms) == set(ARMS):
            case = cases[key[0]]
            groups["/".join((case["workload"], key[2], key[3]))][case["family_id"]].append(
                tuple(arms[arm]["timing_ms"]["terminal"] for arm in ARMS))
    return groups, {"blockers": sorted(blockers), "attempt_inventory": inventory,
                    "results_sha256": fingerprint(results),
                    "measurement_note": "External provider latency excluded by local deterministic responses and zero configured delay; tiny local response processing/observer overhead remains included. Server wait is diagnostic, never subtracted from overlapping task durations."}


def consumption_evidence(attempt, protocol):
    usage = attempt.get("usage", {})
    unknown = [field for field in protocol["required_usage_fields"] if not pilot.number(usage.get(field))]
    optional = [field for field in pilot.USAGE_FIELDS if field not in protocol["required_usage_fields"]]
    optional_unknown = [field for field in optional if usage.get(field) is None]
    gaps = []
    for field in pilot.USAGE_FIELDS:
        if usage.get(field) is not None and (not pilot.number(usage[field])
                                            or field != "cost_usd" and type(usage[field]) is not int):
            gaps.append("invalid_usage_" + field)
    if (all(type(usage.get(field)) is int for field in REQUIRED_USAGE_FIELDS)
            and usage["tokens"] != usage["input_tokens"] + usage["output_tokens"]):
        gaps.append("inconsistent_usage_total")
    events = attempt.get("provider_events")
    if not isinstance(events, list):
        gaps.append("missing_provider_capture_evidence")
    else:
        forwarded = []
        sequences = []
        for event in events:
            if not isinstance(event, dict):
                gaps.append("invalid_provider_capture_event")
                continue
            sequences.append(event.get("sequence"))
            if type(event.get("forwarded")) is not bool or event.get("consumption_unknown") is not False:
                gaps.append("unknown_provider_consumption")
            if event.get("forwarded") is True:
                forwarded.append(event)
                fields = ("input_tokens", "output_tokens", "total_tokens")
                if (event.get("state") != "finished"
                    or any(type(event.get(field)) is not int or event[field] < 0 for field in fields)
                    or event.get("total_tokens") != event.get("input_tokens", 0) + event.get("output_tokens", 0)):
                    gaps.append("unfinished_or_inconsistent_provider_consumption")
        if sequences != list(range(1, len(events) + 1)):
            gaps.append("incomplete_or_duplicate_provider_capture_inventory")
        if usage.get("requests") != len(forwarded):
            gaps.append("capture_request_count_mismatch")
        for reported, captured in (("tokens", "total_tokens"), ("input_tokens", "input_tokens"), ("output_tokens", "output_tokens")):
            values = [event.get(captured) for event in forwarded]
            if all(type(value) is int and value >= 0 for value in values) and usage.get(reported) != sum(values):
                gaps.append("capture_" + reported + "_mismatch")
    ledger = attempt.get("ledger")
    if not isinstance(ledger, dict) or not isinstance(ledger.get("attempts"), list) or not isinstance(ledger.get("tasks"), list):
        gaps.append("missing_durable_ledger_evidence")
    else:
        if attempt.get("arm") == "mortis" and pilot.number(usage.get("requests")) and usage["requests"] > 0 and not ledger["attempts"]:
            gaps.append("missing_mortis_durable_attempts")
        for row in ledger["attempts"]:
            if (not isinstance(row, dict) or row.get("usage_state") != "known"
                or row.get("status") not in {"completed", "failed"}
                or type(row.get("usage_tokens")) is not int or row["usage_tokens"] < 0
                or type(row.get("reserved_tokens")) is not int or row["reserved_tokens"] < 0):
                gaps.append("unknown_or_reserved_durable_consumption")
        amounts = [row.get("usage_tokens") for row in ledger["attempts"] if isinstance(row, dict)]
        if (all(type(amount) is int and amount >= 0 for amount in amounts) and pilot.number(usage.get("tokens"))
                and sum(amounts) > usage["tokens"]):
            gaps.append("durable_consumption_exceeds_captured_total")
        if any(not isinstance(row, dict) or row.get("status") in {"running", "uncertain"} for row in ledger["tasks"]):
            gaps.append("unsettled_durable_task")
    return {"attempt_id": attempt["attempt_id"], "usage": usage, "unknown_fields": unknown,
            "optional_unknown_fields": optional_unknown, "proof_gaps": sorted(set(gaps))}


def score(seal, results, documents=(), evidence=None, performance_results=None):
    cases, planned = verify_seal(seal)
    protocol, evidence = seal["protocol"], evidence or {}
    controlled_groups, controlled = controlled_performance(seal, performance_results)
    require(results.get("schema") == "mortis-engine-acceptance-results/v2"
            and results.get("seal_sha256") == fingerprint(seal), "Results must bind prospective acceptance seal")
    require(timestamp(results["started_at"]) >= timestamp(seal["frozen_at"]), "Acceptance started before protocol freeze")
    attempts, seen, seen_ids = [], set(), set()
    blockers, gaps, resources, consumption, inventory = set(), [], [], [], []
    blockers.update(controlled["blockers"])
    for attempt in results["attempts"]:
        identity = (*pilot.key(attempt), attempt["arm"])
        require(identity in planned and identity not in seen and attempt.get("attempt_id")
                and attempt["attempt_id"] not in seen_ids, "Unplanned/duplicate attempt; retain retries in attempt accounting")
        seen.add(identity)
        seen_ids.add(attempt["attempt_id"])
        case = cases[attempt["case_id"]]
        require(attempt.get("case_digest") == case["case_digest"] and attempt.get("identity") == planned[identity]["identity"],
                "Attempt frozen case/settings/resources/conditions identity mismatch")
        require(len({finding["finding_id"] for finding in attempt["findings"]}) == len(attempt["findings"]), "Duplicate finding identity")
        coverage = attempt.get("coverage", {})
        require(type(coverage.get("assigned_units")) is int and coverage["assigned_units"] == case["expected_assigned_units"]
                and type(coverage.get("validated_units")) is int
                and 0 <= coverage["validated_units"] <= coverage["assigned_units"], "Invalid frozen coverage denominator")
        work_gaps = pilot.required_work_gaps(attempt, case["workload"], results.get("scanner_snapshot", {}).get("sha256"))
        if attempt.get("status") not in {"complete", "completed"} or coverage["validated_units"] != coverage["assigned_units"]:
            work_gaps.append("incomplete_validated_coverage_or_terminal_status")
        if not pilot.number(attempt.get("timing_ms", {}).get("terminal")) or not attempt["timing_ms"]["terminal"]:
            work_gaps.append("missing_queue_to_terminal_duration")
        if attempt.get("timing_origin") != "queued":
            work_gaps.append("queue_time_not_included")
        if work_gaps:
            gaps.append({"attempt_id": attempt["attempt_id"], "gaps": work_gaps})
            blockers.add("incomplete_required_work")
        usage_evidence = consumption_evidence(attempt, protocol)
        consumption.append(usage_evidence)
        if usage_evidence["unknown_fields"] or usage_evidence["proof_gaps"]:
            blockers.add("unknown_consumption")
        resource = attempt.get("resource_evidence", {})
        resource_ok = resource.get("profile_sha256") == protocol["execution_profile_sha256"] and resource.get("within_limits") is True and sha(resource.get("artifact_sha256"))
        resources.append({"attempt_id": attempt["attempt_id"], "evidence": resource, "valid": resource_ok})
        if not resource_ok:
            blockers.add("resource_budget_deadline_evidence_missing_or_violated")
        attempts.append(attempt)
        inventory.append({field: attempt.get(field) for field in ("attempt_id", "case_id", "arm", "repetition", "graph", "cache", "status", "timing_ms", "stages", "outcomes")})
    missing = sorted(set(planned) - seen)
    if missing:
        blockers.add("missing_planned_attempts")
    packet = blind(results, seal["corpus"])
    for document in documents:
        excluded_reviewers = (seal["corpus"]["provenance"]["implementer_ids"]
                              + seal["corpus"]["provenance"]["truth_author_ids"])
        require(document.get("reviewer_id") not in excluded_reviewers,
                "Implementers and truth authors cannot provide independent acceptance judgments")
        if document.get("reviewer_kind") in {"human", "external_expert", "independent_agent"}:
            require(document.get("reviewer_id") in protocol["independent_reviewer_ids"],
                    "Acceptance reviewer identity was not independently designated before outputs")
    normalized = [{**document, "reviewer_kind": "human"} if document.get("reviewer_kind") == "external_expert"
                  else {**document, "reviewer_kind": "agent"} if document.get("reviewer_kind") == "independent_agent"
                  else document for document in documents]
    decisions = pilot.judgments_by_id(normalized, packet)
    rows = pilot.quality_rows(attempts, cases, decisions)
    if any(row["pending"] for row in rows):
        blockers.add("unresolved_adjudication")
    severe_losses = [{"case_id": row["case_id"], "repetition": row["repetition"], "defect": defect}
                     for row in rows if row["arm"] == "mortis" for defect in row["defects"]
                     if defect["severity"] in {"high", "critical"} and not defect["found"]]
    if severe_losses:
        blockers.add("high_critical_loss")
    quality, performance = {}, {}
    pairs = defaultdict(dict)
    for attempt in attempts:
        pairs[pilot.key(attempt)][attempt["arm"]] = attempt
    for comparison in protocol["comparisons"]:
        name = stratum(comparison)
        quality_rows = [row for row in rows if "/".join((row["workload"], *row["stratum"])) == name]
        quality[name] = {"arms": {arm: pilot.metrics([row for row in quality_rows if row["arm"] == arm]) for arm in ARMS},
                         "bounds": quality_bounds(quality_rows, protocol["bootstrap"])}
        for key, arms in pairs.items():
            if "/".join((cases[key[0]]["workload"], key[2], key[3])) != name or set(arms) != set(ARMS):
                continue
            for field in protocol["comparison_usage_fields"]:
                values = [arms[arm].get("usage", {}).get(field) for arm in ARMS]
                if all(pilot.number(value) for value in values) and values[1] > values[0]:
                    blockers.add("paired_provider_consumption_regression")
        performance[name] = {percentile: latency_bound(controlled_groups.get(name, {}), comparison["latency"][percentile], quantile, protocol["bootstrap"])
                             for percentile, quantile in (("p50", .5), ("p95", .95))}
    for cap, metric in CAP_FIELDS.items():
        limit = protocol["spending"][cap]
        values = [attempt.get("usage", {}).get(metric) for attempt in attempts]
        if limit is not None and (not all(pilot.number(value) for value in values) or sum(values) > limit):
            blockers.add("finite_spending_cap_unknown_or_exceeded")
    checks = evidence.get("checks", {})
    evidence_bound = evidence.get("results_sha256") == fingerprint(results) and evidence.get("seal_sha256") == fingerprint(seal)
    reliability = {name: {**checks.get(name, {}), "valid": evidence_bound and checks.get(name, {}).get("status") == "passed"
                         and sha(checks.get(name, {}).get("artifact_sha256"))} for name in protocol["required_reliability_checks"]}
    if not all(item["valid"] for item in reliability.values()):
        blockers.add("reliability_feature_resource_evidence_incomplete")
    independent_reviews = evidence.get("independent_reviews", [])
    covered = set()
    expected_findings = {item["blind_id"] for item in packet["items"]}
    for review in independent_reviews:
        qualifying = [document for document in documents
                      if document.get("reviewer_kind") == review.get("reviewer_kind")
                      and document.get("reviewer_id") == review.get("reviewer_id")
                      and fingerprint(document) == review.get("judgments_sha256")]
        if not (evidence_bound and review.get("reviewer_kind") in protocol["allowed_signoff_kinds"]
                and review.get("reviewer_id") in protocol["independent_reviewer_ids"]
                and review.get("independent") is True and review.get("arm_blinded") is True
                and review.get("status") == "passed" and sha(review.get("artifact_sha256"))
                and review.get("packet_sha256") == fingerprint(packet)):
            continue
        for document in qualifying:
            if document["reviewer_kind"] == "independent_agent" and not all(
                document.get(field) is True for field in
                ("fresh_review_context", "not_involved_in_implementation", "not_truth_author")
            ):
                continue
            covered.update(decision["blind_id"] for decision in document["judgments"])
    if not expected_findings or covered != expected_findings:
        blockers.add("independent_review_pending")
    if any(bound["status"] != "pass" for item in quality.values() for bound in item["bounds"].values()):
        blockers.add("quality_parity_not_established")
    if any(bound["status"] != "pass" for item in performance.values() for bound in item.values()):
        blockers.add("performance_nonregression_not_established")
    return {"schema": "mortis-engine-acceptance-report/v2", "seal_sha256": fingerprint(seal),
            "results_sha256": fingerprint(results), "judgments_sha256": [fingerprint(doc) for doc in documents],
            "evidence_sha256": fingerprint(evidence), "acceptance": "pass" if not blockers else "not_established",
            "blockers": sorted(blockers), "quality": quality, "high_critical_losses": severe_losses,
            "performance": performance, "reliability": reliability, "independent_reviews": independent_reviews, "independently_reviewed_findings": len(covered),
            "controlled_performance": controlled, "live_provider_timings": "Descriptive quality/reliability evidence only; excluded from the performance gate",
            "resources": resources, "consumption": consumption, "required_work_gaps": gaps,
            "attempt_inventory": inventory, "missing_planned_attempts": [list(item) for item in missing],
            "adoption_authorized": False, "limitations": ["Evidence hashes bind supplied records; independent custody and reviewer identities require external verification. Independent-agent signoff is owner-authorized and never constitutes human/external-expert review.",
                                                           "Stretch speedups are descriptive and never acceptance prerequisites."]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    freezing = sub.add_parser("freeze")
    for field in ("protocol", "corpus", "schedule", "performance-schedule", "output"):
        freezing.add_argument("--" + field, type=Path, required=True)
    blinding = sub.add_parser("blind")
    blinding.add_argument("--seal", type=Path, required=True)
    blinding.add_argument("--results", type=Path, required=True)
    blinding.add_argument("--output", type=Path, required=True)
    scoring = sub.add_parser("score")
    for field in ("seal", "results", "evidence", "output"):
        scoring.add_argument("--" + field, type=Path, required=True)
    scoring.add_argument("--judgments", type=Path, action="append", default=[])
    scoring.add_argument("--performance-results", type=Path)
    args = parser.parse_args()
    def load(path):
        return json.loads(path.read_text())
    if args.command == "freeze":
        report = freeze(load(args.protocol), load(args.corpus), load(args.schedule), load(args.performance_schedule))
    elif args.command == "blind":
        seal, results = load(args.seal), load(args.results)
        verify_seal(seal)
        require(results.get("schema") == "mortis-engine-acceptance-results/v2"
                and results.get("seal_sha256") == fingerprint(seal), "Results do not bind prospective acceptance seal")
        require(timestamp(results["started_at"]) >= timestamp(seal["frozen_at"]), "Acceptance started before protocol freeze")
        report = blind(results, seal["corpus"])
    else:
        report = score(load(args.seal), load(args.results), [load(path) for path in args.judgments], load(args.evidence),
                       load(args.performance_results) if args.performance_results else None)
    pilot.write_new(args.output, report)


if __name__ == "__main__":
    main()
