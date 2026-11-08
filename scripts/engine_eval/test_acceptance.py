"""Pure prospective acceptance checks using invented in-memory test evidence only."""

import copy
import json
import tempfile
import unittest
from pathlib import Path

import acceptance


class AcceptanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.protocol = {
            "schema": "mortis-engine-acceptance-protocol/v2", "version": "unit-test-only", "approved_by": "test-owner",
            "repository_scope": ["wilfredinni/code-mortis"],
            "independent_reviewer_ids": ["invented-test-reviewer"],
            "allowed_signoff_kinds": ["human", "external_expert", "independent_agent"],
            "materiality": {"baseline_calibration_sha256": "a" * 64, "rationale": "Invented unit-test threshold", "calibration_kind": "controlled_ocr_engine_overhead", "selected_before_acceptance": True},
            "performance_measurement": {"mode": "deterministic_local_provider", "metric": "queue_to_terminal_ms",
                                        "endpoint_scope": "loopback_only", "configured_provider_delay_ms": 0, "response_fixture_sha256": "f" * 64},
            "bootstrap": {"resamples": 1000, "seed": 17, "confidence": .95, "minimum_families": 20},
            "sampling": {workload: {"minimum_cases": 20, "repetitions": 3} for workload in ("pr", "security")},
            "strata_rationale": "Invented full unit-test grid; real evaluation must justify declared cells before outputs",
            "comparisons": [{"workload": workload, "graph": graph, "cache": cache,
                             "latency": {name: {"absolute_ms": 100, "relative_fraction": .1, "combine": "both"} for name in ("p50", "p95")}}
                            for workload in ("pr", "security") for graph in ("off", "on", "gap") for cache in ("cold", "warm")],
            "required_reliability_checks": sorted(acceptance.RELIABILITY_CHECKS),
            "required_corpus_strata": sorted(acceptance.CORPUS_STRATA), "execution_profile_sha256": "a" * 64,
            "required_usage_fields": sorted(acceptance.REQUIRED_USAGE_FIELDS), "comparison_usage_fields": ["requests", "tokens"],
            "spending": {"mode": "explicit_owner_uncapped", "stop_on_unknown_usage": True,
                         "aggregate_request_cap": None, "aggregate_token_cap": None, "aggregate_usd_cap": None},
        }
        cls.corpus = {
            "schema": "mortis-engine-acceptance-corpus/v2", "sealed_heldout": True, "implementer_visible": False,
            "repositories": ["wilfredinni/code-mortis"],
            "strata_evidence": dict.fromkeys(acceptance.CORPUS_STRATA, "b" * 64),
            "cases": [{"case_id": f"{workload}-{i}", "family_id": f"{workload}-family-{i}", "workload": workload, "repository": "wilfredinni/code-mortis",
                       "case_digest": "c" * 64, "expected_assigned_units": 1, "evaluation_track": "quality_microcase",
                       "source_evidence": {"head": {"test.py": "invented test source"}},
                       "truth": {"expected": "defect", "defects": [{"id": "d1", "severity": "high",
                           **dict.fromkeys((*acceptance.pilot.SEMANTIC_FIELDS, "proof"), "Invented test evidence")}]}}
                      for workload in ("pr", "security") for i in range(20)],
        }
        for case in cls.corpus["cases"]:
            if case["case_id"].endswith("-1"):
                case["truth"]["defects"][0]["severity"] = "critical"
            elif case["case_id"].endswith("-2"):
                case["truth"] = {"expected": "clean", "defects": []}
        cls.corpus["provenance"] = {
            "custodian_id": "invented-test-custodian", "reviewer_id": "invented-test-reviewer",
            "implementer_ids": ["invented-test-implementer"], "truth_author_ids": ["invented-test-truth-author"], "review_packet_sha256": "a" * 64,
            "source_manifests_sha256": "a" * 64, "sealed_at": "2026-11-07T00:00:00+00:00",
            "reviewed_cases_sha256": acceptance.fingerprint([
                {"case_id": case["case_id"], "case_digest": case["case_digest"], "truth_sha256": acceptance.fingerprint(case["truth"])}
                for case in cls.corpus["cases"]]),
        }
        cls.corpus["provenance_evidence_sha256"] = acceptance.fingerprint(cls.corpus["provenance"])
        cls.schedule = [{"case_id": case["case_id"], "arm": arm, "repetition": repetition,
                         "graph": comparison["graph"], "cache": comparison["cache"],
                         "identity": dict.fromkeys(acceptance.IDENTITIES, "d" * 64)}
                        for case in cls.corpus["cases"] for comparison in cls.protocol["comparisons"]
                        if comparison["workload"] == case["workload"]
                        for repetition in range(1, 4) for arm in acceptance.ARMS]
        cls.protocol["performance_sampling"] = copy.deepcopy(cls.protocol["sampling"])
        cls.performance_plan = {"schema": "mortis-engine-controlled-performance-plan/v2",
                                "analysis_concurrency": 4,
                                "response_fixture_sha256": "f" * 64, "semantic_equivalence_evidence_sha256": "a" * 64,
                                "family_provenance_sha256": "a" * 64, "required_behaviors": sorted(acceptance.PERFORMANCE_BEHAVIORS),
                                "cases": [{**{field: case[field] for field in ("family_id", "workload", "case_digest", "expected_assigned_units")},
                                           "expected_assigned_units": 2, "case_id": "perf-" + case["case_id"]} for case in cls.corpus["cases"]],
                                "schedule": [{**item, "case_id": "perf-" + item["case_id"]} for item in cls.schedule]}
        for case in cls.performance_plan["cases"]:
            clean = case["case_id"].endswith("-2")
            case["expected_assigned_units"] = 1 if clean else 5 if case["workload"] == "security" else 2
            case["expected_work"] = {"findings": 0 if clean else 1, "assessment_candidates": 0 if clean else 1,
                                     "historical_verification_targets": 0 if clean else 1,
                                     "source_files": case["expected_assigned_units"],
                                     "security_analysis_tasks": (1 if clean else 5) if case["workload"] == "security" else 0}

    def seal(self):
        return acceptance.freeze(copy.deepcopy(self.protocol), copy.deepcopy(self.corpus), copy.deepcopy(self.schedule), copy.deepcopy(self.performance_plan),
                                 frozen_at="2026-11-08T00:00:00+00:00")

    def results(self, seal):
        attempts = []
        for arm in acceptance.ARMS:
            scheduled = next(item for item in self.schedule if item["case_id"] == "pr-0" and item["graph"] == "off" and item["cache"] == "cold" and item["repetition"] == 1 and item["arm"] == arm)
            attempts.append({**copy.deepcopy(scheduled), "attempt_id": "test-" + arm, "case_digest": "c" * 64,
                             "status": "complete", "findings": [{"finding_id": "f1", "body": "Invented test finding"}],
                             "coverage": {"assigned_units": 1, "validated_units": 1},
                             "timing_ms": {"terminal": 1000}, "timing_origin": "queued", "stages": {},
                             "summary": {"status": "completed"}, "progress": {"analysis_status": "completed", "verification": dict.fromkeys(("selected", "completed", "failed", "skipped", "uncertain", "pending", "sync_failed"), 0)},
                             "candidates": [], "usage": dict.fromkeys(acceptance.pilot.USAGE_FIELDS, 0), "outcomes": [],
                             "provider_events": [], "ledger": {"attempts": [], "tasks": []},
                             "resource_evidence": {"profile_sha256": "a" * 64, "within_limits": True, "artifact_sha256": "e" * 64}})
        return {"schema": "mortis-engine-acceptance-results/v2", "seal_sha256": acceptance.fingerprint(seal),
                "started_at": "2026-11-08T00:00:01+00:00", "attempts": attempts}

    def judgments(self, results, seal):
        packet = acceptance.blind(results, seal["corpus"])
        return [{"schema": "mortis-engine-judgments/v1", "packet_sha256": acceptance.fingerprint(packet),
                 "reviewer_id": "invented-test-reviewer", "reviewer_kind": "agent", "arm_blinded": True, "independent": True,
                 "judgments": [{"blind_id": item["blind_id"], "verdict": "match", "defect_id": "d1",
                                "semantic": dict.fromkeys(acceptance.pilot.SEMANTIC_FIELDS, True),
                                "citation_bounds_valid": True, "rationale": "Invented unit-test judgment"} for item in packet["items"]]}]

    def test_freeze_seals_all_inputs_and_is_create_only(self):
        seal = self.seal()
        acceptance.verify_seal(seal)
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "seal.json"
            acceptance.pilot.write_new(output, seal)
            with self.assertRaises(FileExistsError):
                acceptance.pilot.write_new(output, seal)
            self.assertEqual(json.loads(output.read_text()), seal)
        for field in ("protocol", "corpus", "schedule", "performance_schedule"):
            changed = copy.deepcopy(seal)
            changed[field] = []
            with self.assertRaisesRegex(ValueError, "drift"):
                acceptance.verify_seal(changed)

    def test_pending_thresholds_calibration_or_sampling_cannot_freeze(self):
        for change in (
            lambda p: p.update(approved_by=None),
            lambda p: p["materiality"].update(baseline_calibration_sha256=None),
            lambda p: p["comparisons"][0]["latency"]["p50"].update(relative_fraction=None),
            lambda p: p["comparisons"][0]["latency"]["p95"].update(combine=None),
            lambda p: p["sampling"]["security"].update(minimum_cases=None),
            lambda p: p["sampling"]["pr"].update(repetitions=1),
            lambda p: p["bootstrap"].update(minimum_families=1),
            lambda p: p["comparisons"].__setitem__(slice(None), [item for item in p["comparisons"] if item["graph"] != "gap"]),
        ):
            with self.subTest(change=change):
                protocol = copy.deepcopy(self.protocol)
                change(protocol)
                with self.assertRaises(ValueError):
                    acceptance.freeze(protocol, self.corpus, self.schedule)

    def test_visible_unsealed_or_inadequate_corpus_cannot_freeze(self):
        for change in (
            lambda c: c.update(implementer_visible=True),
            lambda c: c.update(sealed_heldout=False),
            lambda c: c["strata_evidence"].pop("critical_defects"),
            lambda c: c["cases"].pop(),
            lambda c: [case.update(family_id="same-family") for case in c["cases"]],
        ):
            corpus = copy.deepcopy(self.corpus)
            change(corpus)
            with self.assertRaises(ValueError):
                acceptance.freeze(self.protocol, corpus, self.schedule)

    def test_missing_duplicate_or_mismatched_pair_schedule_cannot_freeze(self):
        for schedule in (self.schedule[:-1], self.schedule + [self.schedule[0]]):
            with self.assertRaises(ValueError):
                acceptance.freeze(self.protocol, self.corpus, schedule)
        schedule = copy.deepcopy(self.schedule)
        schedule[0]["identity"][acceptance.IDENTITIES[0]] = "f" * 64
        with self.assertRaisesRegex(ValueError, "identity mismatch"):
            acceptance.freeze(self.protocol, self.corpus, schedule)

    def test_declared_strata_need_not_be_cartesian_and_repetitions_use_frozen_rationale(self):
        protocol = copy.deepcopy(self.protocol)
        protocol["comparisons"] = [item for item in protocol["comparisons"]
                                   if (item["graph"], item["cache"]) in {("off", "cold"), ("on", "warm"), ("gap", "cold")}]
        for sample in protocol["sampling"].values():
            sample.update(repetitions=2, repetition_rationale="Invented scheduling constraint declared before test outputs")
        acceptance.validate_protocol(protocol)

    def test_latency_combination_rule_is_applied_in_bootstrap_statistic(self):
        families = {f"f{i}": [(1000 + i * 31, (1000 + i * 31) * 1.05 + i)] for i in range(20)}
        bootstrap = self.protocol["bootstrap"]
        both = acceptance.latency_bound(families, {"absolute_ms": 0, "relative_fraction": .2, "combine": "both"}, .5, bootstrap)
        either = acceptance.latency_bound(families, {"absolute_ms": 0, "relative_fraction": .2, "combine": "either"}, .5, bootstrap)
        self.assertEqual(both["status"], "not_established")
        self.assertEqual(either["status"], "pass")
        self.assertGreater(both["one_sided_95_upper_excess_ms"], 0)
        self.assertLess(either["one_sided_95_upper_excess_ms"], 0)
        self.assertFalse(either["stretch_point_met"])

    def test_latency_repetitions_do_not_create_independent_families(self):
        families = {"one": [(1000, 500 + i) for i in range(100)]}
        result = acceptance.latency_bound(families, {"absolute_ms": 100, "relative_fraction": .1, "combine": "both"}, .95, self.protocol["bootstrap"])
        self.assertEqual(result["independent_families"], 1)
        self.assertEqual(result["status"], "inconclusive")
        self.assertIn("inadequate_independent_families", result["reasons"])

    def test_identical_degenerate_latency_is_not_an_acceptance_pass(self):
        result = acceptance.latency_bound({str(i): [(1000, 1000)] for i in range(20)},
                                          {"absolute_ms": 100, "relative_fraction": .1, "combine": "both"}, .5,
                                          self.protocol["bootstrap"])
        self.assertEqual(result["status"], "inconclusive")
        self.assertIn("degenerate_bootstrap", result["reasons"])

    def test_percentile_improvement_cannot_compensate_for_tail_regression(self):
        families = {str(i): [(1000 + i * 31, (1000 + i * 31) * (.8 if i < 17 else 1.5))] for i in range(20)}
        margin = {"absolute_ms": 100, "relative_fraction": .1, "combine": "both"}
        median = acceptance.latency_bound(families, margin, .5, self.protocol["bootstrap"])
        tail = acceptance.latency_bound(families, margin, .95, self.protocol["bootstrap"])
        self.assertEqual(median["status"], "pass")
        self.assertEqual(tail["status"], "not_established")

    def test_quality_keeps_no_worse_lower_bound_rule(self):
        rows = []
        for i in range(20):
            for arm in acceptance.ARMS:
                improved = arm == "mortis"
                rows.append({"family_id": str(i), "arm": arm, "tp": 1 + i % 3 + improved,
                             "fp": 0 if improved else i % 2, "fn": 0 if improved else 1, "pending": 0,
                             "defects": [], "clean": False, "assigned_units": 1, "validated_units": 1,
                             "valid_citations": 0, "judged_citations": 0, "semantic_supported": 0, "judged_semantic": 0})
        bounds = acceptance.quality_bounds(rows, self.protocol["bootstrap"])
        self.assertTrue(all(item["status"] == "pass" for item in bounds.values()))
        for row in rows:
            if row["arm"] == "mortis":
                row["fp"] = 10
        self.assertEqual(acceptance.quality_bounds(rows, self.protocol["bootstrap"])["precision"]["status"], "not_established")

    def test_actual_designated_external_expert_document_is_required(self):
        seal = self.seal()
        results = self.results(seal)
        documents = self.judgments(results, seal)
        documents[0]["reviewer_kind"] = "external_expert"
        packet = acceptance.blind(results, seal["corpus"])
        evidence = {"seal_sha256": acceptance.fingerprint(seal), "results_sha256": acceptance.fingerprint(results),
                    "independent_reviews": [{"reviewer_id": "invented-test-reviewer", "reviewer_kind": "external_expert",
                                      "independent": True, "arm_blinded": True, "status": "passed",
                                      "artifact_sha256": "a" * 64, "packet_sha256": acceptance.fingerprint(packet),
                                      "judgments_sha256": acceptance.fingerprint(documents[0])}]}
        report = acceptance.score(seal, results, documents, evidence)
        self.assertNotIn("independent_review_pending", report["blockers"])
        evidence["independent_reviews"][0]["judgments_sha256"] = "f" * 64
        report = acceptance.score(seal, results, documents, evidence)
        self.assertIn("independent_review_pending", report["blockers"])
        documents[0]["reviewer_id"] = "implementer-agent-pretending-to-be-human"
        documents[0]["reviewer_kind"] = "human"
        with self.assertRaisesRegex(ValueError, "not independently designated"):
            acceptance.score(seal, results, documents, evidence)

    def test_agent_signoff_is_explicit_complete_fresh_and_never_human(self):
        seal = self.seal()
        results = self.results(seal)
        documents = self.judgments(results, seal)
        documents[0].update(reviewer_kind="independent_agent", fresh_review_context=True,
                            not_involved_in_implementation=True, not_truth_author=True)
        review = {"reviewer_id": "invented-test-reviewer", "reviewer_kind": "independent_agent", "independent": True,
                  "arm_blinded": True, "status": "passed", "artifact_sha256": "a" * 64,
                  "packet_sha256": acceptance.fingerprint(acceptance.blind(results, seal["corpus"])),
                  "judgments_sha256": acceptance.fingerprint(documents[0])}
        evidence = {"seal_sha256": acceptance.fingerprint(seal), "results_sha256": acceptance.fingerprint(results), "independent_reviews": [review]}
        report = acceptance.score(seal, results, documents, evidence)
        self.assertNotIn("independent_review_pending", report["blockers"])
        self.assertEqual(report["independent_reviews"][0]["reviewer_kind"], "independent_agent")
        self.assertEqual(report["independently_reviewed_findings"], 2)
        empty = {**documents[0], "judgments": []}
        review["judgments_sha256"] = acceptance.fingerprint(empty)
        documents[0]["reviewer_kind"] = "agent"
        report = acceptance.score(seal, results, [documents[0], empty], evidence)
        self.assertIn("independent_review_pending", report["blockers"])

    def test_verification_rechecks_custody_chronology(self):
        seal = self.seal()
        seal["frozen_at"] = "2026-11-06T00:00:00+00:00"
        with self.assertRaisesRegex(ValueError, "custody"):
            acceptance.verify_seal(seal)

    def test_live_provider_timings_are_descriptive_only(self):
        seal = self.seal()
        live = self.results(seal)
        report = acceptance.score(seal, live, self.judgments(live, seal))
        self.assertIn("controlled_performance_results_pending", report["blockers"])
        self.assertEqual(report["performance"]["pr/off/cold"]["p50"]["reasons"], ["no_complete_pairs"])
        with self.assertRaisesRegex(ValueError, "Live quality/provider-network"):
            acceptance.controlled_performance(seal, live)

    def test_controlled_latency_uses_full_nontrivial_work_and_no_subtraction(self):
        seal = self.seal()
        controlled = self.results(seal)
        controlled.update(schema="mortis-engine-controlled-performance-results/v2", measurement=seal["protocol"]["performance_measurement"])
        for attempt in controlled["attempts"]:
            attempt.update(case_id="perf-" + attempt["case_id"], attempt_id="perf-" + attempt["attempt_id"],
                           coverage={"assigned_units": 2, "validated_units": 2},
                           candidates=[{"status": "completed"}],
                           verification_selection={"observed": True, "selected": 1},
                           verification_targets=[{"status": "completed", "verdict": "still_present"}],
                           nontrivial_work_evidence={"findings": 1, "assessment_candidates": 1, "historical_verification_targets": 1,
                                                    "source_files": 2, "security_analysis_tasks": 0,
                                                    "source_paths": ["a.py", "b.py"], "artifact_sha256": "e" * 64},
                           performance_evidence={"measurement": seal["protocol"]["performance_measurement"], "artifact_sha256": "e" * 64, "server_wait_ms": 2})
            attempt["transport"] = {"provider_mode": "controlled_synthetic_http",
                                    "declared_connection": {"base_url": "http://127.0.0.1:12345/v1"}}
            attempt["timing_ms"] = {"terminal": 1234 if attempt["arm"] == "mortis" else 1000, "provider_sum": 999999}
        groups, report = acceptance.controlled_performance(seal, controlled)
        self.assertEqual(groups["pr/off/cold"]["pr-family-0"], [(1000, 1234)])
        self.assertNotIn("controlled_required_work_timing_or_resource_evidence_incomplete", report["blockers"])
        controlled["attempts"][0]["candidates"] = []
        _, report = acceptance.controlled_performance(seal, controlled)
        self.assertIn("controlled_required_work_timing_or_resource_evidence_incomplete", report["blockers"])
        controlled["attempts"][0]["attempt_id"] = ""
        with self.assertRaisesRegex(ValueError, "duplicate controlled"):
            acceptance.controlled_performance(seal, controlled)

    def test_declared_clean_single_file_controlled_case_is_valid(self):
        seal = self.seal()
        controlled = self.results(seal)
        controlled.update(schema="mortis-engine-controlled-performance-results/v2", measurement=seal["protocol"]["performance_measurement"])
        for attempt in controlled["attempts"]:
            attempt.update(case_id="perf-pr-2", attempt_id="clean-" + attempt["attempt_id"], findings=[],
                           verification_selection={"observed": True, "selected": 0}, verification_targets=[],
                           nontrivial_work_evidence={"findings": 0, "assessment_candidates": 0, "historical_verification_targets": 0,
                                                    "source_files": 1, "security_analysis_tasks": 0,
                                                    "source_paths": ["a.py"], "artifact_sha256": "e" * 64},
                           performance_evidence={"measurement": seal["protocol"]["performance_measurement"], "artifact_sha256": "e" * 64})
            attempt["transport"] = {"provider_mode": "controlled_synthetic_http",
                                    "declared_connection": {"base_url": "http://127.0.0.1:12345/v1"}}
        groups, report = acceptance.controlled_performance(seal, controlled)
        self.assertEqual(groups["pr/off/cold"]["pr-family-2"], [(1000, 1000)])
        self.assertNotIn("controlled_required_work_timing_or_resource_evidence_incomplete", report["blockers"])

    def controlled_security(self, seal):
        result = self.results(seal)
        result.update(schema="mortis-engine-controlled-performance-results/v2", measurement=seal["protocol"]["performance_measurement"],
                      scanner_snapshot={"sha256": "f" * 64})
        for attempt in result["attempts"]:
            attempt.pop("ledger", None)
            plan = next(item for item in self.performance_plan["schedule"] if item["case_id"] == "perf-security-0"
                        and item["graph"] == "off" and item["cache"] == "cold" and item["repetition"] == 1 and item["arm"] == attempt["arm"])
            attempt.update(copy.deepcopy(plan))
            paths = [f"source_{index}.py" for index in range(5)]
            tasks = {"analysis:" + path: {"status": "completed", "input_hash": "a" * 64,
                                          "result": {"findings": [], "required_paths": []}} for path in paths}
            tasks["assessment:0"] = {"status": "completed", "input_hash": "b" * 64, "result": {"verdict": "confirmed"}}
            tasks["verification:original-finding"] = {"status": "completed", "input_hash": "c" * 64, "result": {"verdict": "still_present"}}
            attempt.update(coverage={"assigned_units": 5, "validated_units": 5}, progress={"tasks": tasks},
                           stages={name: {"status": "completed"} for name in ("snapshot", "trivy", "analysis", "assessment", "verification", "report")},
                           report={"coverage": {"trivy": {"evaluation_snapshot": {"before": "f" * 64, "after": "f" * 64}}}},
                           nontrivial_work_evidence={"findings": 1, "assessment_candidates": 1, "historical_verification_targets": 1,
                                                    "source_files": 5, "security_analysis_tasks": 5, "source_paths": paths, "artifact_sha256": "e" * 64},
                           transport={"provider_mode": "controlled_synthetic_http", "declared_connection": {"base_url": "http://127.0.0.1:12345/v1"}},
                           performance_evidence={"measurement": seal["protocol"]["performance_measurement"], "artifact_sha256": "e" * 64})
            attempt["stages"]["analysis"].update(files=5, completed=5, failed=0)
            attempt["stages"]["assessment"].update(total=1, completed=1, failed=0)
            attempt["stages"]["verification"].update(total=1, completed=1)
            if attempt["arm"] == "mortis":
                attempt["ledger"] = {"tasks": [{"id": "gateway-task", "stage": "security", "status": "completed"}]}
        return result

    def test_security_counts_bind_shared_ocr_and_mortis_checkpoints(self):
        seal = self.seal()
        results = self.controlled_security(seal)
        groups, report = acceptance.controlled_performance(seal, results)
        self.assertEqual(groups["security/off/cold"]["security-family-0"], [(1000, 1000)])
        self.assertNotIn("controlled_required_work_timing_or_resource_evidence_incomplete", report["blockers"])
        self.assertNotIn("ledger", results["attempts"][0])
        for mutate in (
            lambda a: a["progress"]["tasks"].pop("assessment:0"),
            lambda a: a["progress"]["tasks"]["analysis:source_0.py"].update(status="failed"),
            lambda a: a["progress"]["tasks"]["analysis:source_0.py"].pop("input_hash"),
            lambda a: a["stages"]["verification"].update(total=0, completed=0),
            lambda a: a["stages"]["assessment"].update(total=True, completed=True),
            lambda a: a["nontrivial_work_evidence"].update(source_paths=[f"other_{i}.py" for i in range(5)]),
        ):
            with self.subTest(mutate=mutate):
                changed = copy.deepcopy(results)
                mutate(changed["attempts"][0])
                _, report = acceptance.controlled_performance(seal, changed)
                self.assertIn("controlled_required_work_timing_or_resource_evidence_incomplete", report["blockers"])

    def test_unrelated_gateway_tasks_cannot_stand_in_for_security_analysis(self):
        seal = self.seal()
        results = self.controlled_security(seal)
        attempt = results["attempts"][0]
        attempt["progress"]["tasks"] = {key: task for key, task in attempt["progress"]["tasks"].items() if not key.startswith("analysis:")}
        attempt["ledger"] = {"tasks": [{"id": f"assessment-{index}", "status": "completed"} for index in range(5)]}
        attempt["nontrivial_work_evidence"]["security_analysis_task_ids"] = [task["id"] for task in attempt["ledger"]["tasks"]]
        _, report = acceptance.controlled_performance(seal, results)
        self.assertIn("security_analysis_counts_do_not_match_retained_tasks", report["attempt_inventory"][0]["required_work_gaps"])

    def test_security_task_count_cannot_exceed_frozen_assigned_sources(self):
        plan = copy.deepcopy(self.performance_plan)
        case = next(case for case in plan["cases"] if case["workload"] == "security")
        case["expected_work"]["security_analysis_tasks"] += 1
        with self.assertRaisesRegex(ValueError, "Security analysis counts"):
            acceptance.validate_performance_plan(plan, {}, self.protocol)

    def test_live_or_external_upstream_cannot_be_relabelled_controlled(self):
        seal = self.seal()
        for mode, upstream in (("live", "http://127.0.0.1:12345/v1"),
                               ("controlled_synthetic_http", "https://provider.example/v1"),
                               ("controlled_synthetic_http", "http://provider.example/v1")):
            with self.subTest(mode=mode, upstream=upstream):
                results = self.controlled_security(seal)
                results["attempts"][0]["transport"] = {"provider_mode": mode, "declared_connection": {"base_url": upstream}}
                _, report = acceptance.controlled_performance(seal, results)
                self.assertIn("controlled_required_work_timing_or_resource_evidence_incomplete", report["blockers"])

    def test_partial_run_reports_evidence_separately_without_acceptance(self):
        seal = self.seal()
        results = self.results(seal)
        report = acceptance.score(seal, results, self.judgments(results, seal))
        self.assertEqual(report["acceptance"], "not_established")
        self.assertIn("missing_planned_attempts", report["blockers"])
        self.assertIn("independent_review_pending", report["blockers"])
        self.assertNotIn("unresolved_adjudication", report["blockers"])
        self.assertNotIn("high_critical_loss", report["blockers"])
        self.assertNotIn("unknown_consumption", report["blockers"])
        self.assertEqual(report["required_work_gaps"], [])
        self.assertEqual(len(report["resources"]), 2)
        self.assertEqual(len(report["consumption"]), 2)
        self.assertFalse(report["adoption_authorized"])

    def metered_attempt(self):
        seal = self.seal()
        attempt = self.results(seal)["attempts"][1]
        attempt["usage"].update(requests=1, tokens=12, input_tokens=9, output_tokens=3,
                                cost_usd=None, cache_write_tokens=None, reasoning_tokens=None)
        attempt["provider_events"] = [{"sequence": 1, "forwarded": True, "state": "finished", "consumption_unknown": False,
                                       "input_tokens": 9, "output_tokens": 3, "total_tokens": 12}]
        attempt["ledger"] = {"tasks": [{"id": "task", "status": "completed"}],
                             "attempts": [{"status": "completed", "usage_state": "known", "usage_tokens": 12, "reserved_tokens": 100}]}
        return attempt

    def test_optional_provider_breakdowns_remain_unknown_without_blocking_known_consumption(self):
        row = acceptance.consumption_evidence(self.metered_attempt(), self.protocol)
        self.assertEqual(row["unknown_fields"], [])
        self.assertEqual(row["proof_gaps"], [])
        self.assertTrue({"cost_usd", "cache_write_tokens", "reasoning_tokens"} <= set(row["optional_unknown_fields"]))
        self.assertIsNone(row["usage"]["cost_usd"])

    def test_capture_and_ledger_unknowns_cannot_hide_behind_known_top_level_usage(self):
        for change in (
            lambda a: a.pop("provider_events"),
            lambda a: a.pop("ledger"),
            lambda a: a["ledger"].update(attempts=[]),
            lambda a: a["provider_events"][0].update(state="started"),
            lambda a: a["provider_events"][0].update(consumption_unknown=True),
            lambda a: a["provider_events"][0].update(total_tokens=11),
            lambda a: a["provider_events"][0].update(sequence=2),
            lambda a: a["usage"].update(requests=0),
            lambda a: a["usage"].update(tokens=11),
            lambda a: a["ledger"]["attempts"][0].update(usage_state="reserved", usage_tokens=None),
            lambda a: a["ledger"]["attempts"][0].update(status="running"),
            lambda a: a["ledger"]["attempts"][0].update(usage_state="unknown"),
            lambda a: a["ledger"]["attempts"][0].update(usage_state="unknown_acknowledged"),
            lambda a: a["ledger"]["attempts"][0].update(usage_tokens=13),
            lambda a: a["ledger"]["tasks"][0].update(status="running"),
            lambda a: a["usage"].update(cache_write_tokens=-1),
        ):
            with self.subTest(change=change):
                attempt = self.metered_attempt()
                change(attempt)
                self.assertTrue(acceptance.consumption_evidence(attempt, self.protocol)["proof_gaps"])

    def test_missing_required_usage_and_finite_usd_cap_stay_fail_closed(self):
        attempt = self.metered_attempt()
        attempt["usage"]["tokens"] = None
        self.assertIn("tokens", acceptance.consumption_evidence(attempt, self.protocol)["unknown_fields"])
        protocol = copy.deepcopy(self.protocol)
        protocol["spending"].update(mode="finite", aggregate_usd_cap=1)
        with self.assertRaisesRegex(ValueError, "finite cap"):
            acceptance.validate_protocol(protocol)
        protocol["required_usage_fields"].append("cost_usd")
        with self.assertRaisesRegex(ValueError, "no observed USD billing"):
            acceptance.validate_protocol(protocol)
        self.assertIn("cost_usd", acceptance.consumption_evidence(attempt, protocol)["unknown_fields"])

    def test_paired_requests_and_total_tokens_regressions_remain_required(self):
        seal = self.seal()
        results = self.results(seal)
        results["attempts"][1] = self.metered_attempt()
        report = acceptance.score(seal, results, self.judgments(results, seal))
        self.assertIn("paired_provider_consumption_regression", report["blockers"])
        self.assertNotIn("unknown_consumption", report["blockers"])

    def test_unknown_or_exceeded_finite_cap_cannot_pass(self):
        protocol = copy.deepcopy(self.protocol)
        protocol["spending"].update(mode="finite", aggregate_request_cap=0)
        seal = acceptance.freeze(protocol, self.corpus, self.schedule, self.performance_plan,
                                 frozen_at="2026-11-08T00:00:00+00:00")
        results = self.results(seal)
        results["attempts"][1] = self.metered_attempt()
        report = acceptance.score(seal, results, self.judgments(results, seal))
        self.assertIn("finite_spending_cap_unknown_or_exceeded", report["blockers"])

    def test_unknown_usage_required_work_resource_and_severe_loss_fail_closed(self):
        seal = self.seal()
        results = self.results(seal)
        mortis = results["attempts"][1]
        mortis["findings"] = []
        mortis["usage"]["tokens"] = None
        mortis["summary"]["status"] = "failed"
        mortis["resource_evidence"]["within_limits"] = False
        report = acceptance.score(seal, results, self.judgments(results, seal))
        for blocker in ("high_critical_loss", "unknown_consumption", "incomplete_required_work", "resource_budget_deadline_evidence_missing_or_violated"):
            self.assertIn(blocker, report["blockers"])
        self.assertTrue(report["required_work_gaps"])
        self.assertEqual(report["consumption"][1]["unknown_fields"], ["tokens"])
        self.assertEqual(report["high_critical_losses"][0]["defect"]["id"], "d1")

    def test_pending_judgment_never_counts_as_true_positive(self):
        seal = self.seal()
        results = self.results(seal)
        report = acceptance.score(seal, results)
        self.assertIn("unresolved_adjudication", report["blockers"])
        self.assertEqual(report["quality"]["pr/off/cold"]["arms"]["mortis"]["tp"], 0)

    def test_results_require_prospective_seal_and_original_identities(self):
        seal = self.seal()
        for change in (
            lambda r: r.update(started_at="2026-11-07T00:00:00+00:00"),
            lambda r: r.update(schema="mortis-engine-eval-results/v1"),
            lambda r: r.update(seal_sha256="f" * 64),
            lambda r: r["attempts"][0]["identity"].update(source_sha256="f" * 64),
            lambda r: r["attempts"].append(r["attempts"][0]),
            lambda r: r["attempts"][0]["coverage"].update(assigned_units=2),
        ):
            results = self.results(seal)
            change(results)
            with self.assertRaises(ValueError):
                acceptance.score(seal, results)


if __name__ == "__main__":
    unittest.main()
