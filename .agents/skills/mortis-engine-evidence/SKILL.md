---
name: mortis-engine-evidence
description: Inspect, validate, or rescore retained Code Mortis engine evaluation evidence; prepare a prospective evaluation plan when requested. Use for quality, reliability, consumption, or performance claims, not ordinary unit testing or starting a new review.
---

# Assess Code Mortis engine evidence

Read `AGENTS.md` and resolve paths from the repository root. Start with the requested
claim and any supplied manifest, seal and results. The companion `mortis-benchmarks`
project contains the protocols and scorers. Use the original protocol and
thresholds for retained evidence.

## Identify the evidence before interpreting it

Record the requested claim, evidence location, source revision/image, workload,
schema/protocol, corpus and split, provider/model, graph/cache strata, repetitions,
and frozen input identities. Read only the records needed for that claim. If results
or a seal were not supplied or otherwise authorized for read access, report the
evidence gap. The public protocols and synthetic fixtures support planning and offline
checks on their own.

Distinguish these outcomes explicitly:
- Offline tests validate implementation behavior on synthetic fixtures.
- Retained live runs describe their pinned versions and finite corpus.
- Frozen experimental acceptance follows that run's original protocol and seal.
- Scope engineering approvals to the decision they actually cover.
- Broad quality/performance or production acceptance needs its own evidence.

## Use the supported offline path

Inspect the relevant CLI and its `--help` before composing a command:

| Evidence family | Owning tooling |
| --- | --- |
| Original pilot scoring and blinded packets | `engine-eval/harness/score.py` and `engine-eval/harness/scoring_protocol.json` |
| Sealed acceptance and controlled performance | `engine-eval/harness/acceptance.py` with the protocol embedded in the supplied seal. Use `engine-eval/harness/acceptance_protocol.json` as a starting point for prospective planning. |
| Historical launchers and execution retirement | `engine-eval/harness/test_retirement.py` and the launchers' entry-point guards |
| Offline scorer regressions | `engine-eval/harness/test_score.py` and `engine-eval/harness/test_acceptance.py` |

For explicitly requested rescoring, use the matching scorer with the supplied records
and a fresh output path. Preserve original seals, artifacts, judgments, hashes and
reports. Label a report produced by newer scorer code as a new interpretation and
record that revision; do not overwrite the original result or silently change thresholds.
Do not run a new freeze over old outputs to make a retrospective result prospective.

The archived OCR and native-client entry points under `engine-eval/harness` and
`code-graph/harness` reject new execution. Keep their guards intact and use the
supported offline tests to verify them.

## Check the substance of a claim

- Reconcile planned and observed attempts, failures, cancellations, incomplete work,
  uncertain assessments, coverage denominators and accounting before summarizing rates.
- Keep unknown usage unknown. Include repairs, evidence expansion and failed requests;
  reported tokens are not measured billing. Do not discard unfavorable pairs.
- Preserve unresolved independent judgments. Independent-agent signoff is not human
  review; do not give a blinded reviewer the truth key or intended conclusion.
- Keep provider timing descriptive when the sealed performance contract excludes it.
  Never subtract summed concurrent provider durations from wall time as engine overhead.
- Distinguish complete workload and comparable strata from partial execution. Report
  uncertainty, sample limitations and missing scheduled work; do not infer statistical
  acceptance from a favorable median or a scoped owner decision.
- New source fixes have local regression evidence until a compatible new evaluation
  measures them. They do not retroactively repair historical findings or results.

## A requested future run is a separate decision

Prepare the concrete scope, supported current harness, frozen protocol/corpus, isolation,
resource limits, provider/model, spending policy and stop/recovery conditions first.
Reuse explicit authorization in the current task if it covers those actions; otherwise
obtain the missing decision before live execution. Historical spending approvals and
unlimited application settings are not authorization for a new run. If no supported
Mortis-only harness exists for the requested experiment, identify that gap rather than
reviving the retired comparison runner. No provider probe, service pause, publication,
deployment or long benchmark follows implicitly from evidence analysis.

Return the supported conclusion, source artifact identities, relevant counts/metrics,
protocol decision, unresolved limitations, and any fresh offline output paths. Clearly
label what was read, recomputed or merely proposed.
