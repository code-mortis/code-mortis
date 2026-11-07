# M0 pilot corpus and M1 handoff

Prepared 2026-11-07 from `9d91f04e964953e350b64c96bf34c50a17a69439`. This completes M0's requirements and pilot preparation. M1 implementation has not started. No production data, provider credential, external inference, publication, or application runtime was changed.

## Frozen artifacts

- [Pilot manifest](scripts/engine_eval/pilot_manifest.json): 12 PR and eight security cases, complete source maps, deterministic Git base/head/merge-base/default-branch commits, trees/blobs, diffs, selected/unavailable/excluded units and hashes.
- [Pilot profile](scripts/engine_eval/pilot_profile.json): selected shared provider/model, explicit uncapped spending preference, dated public price snapshot, local Docker inventory and chosen execution ceilings.
- [Independent adjudication](scripts/engine_eval/reviews/adjudication.json): two nonauthor reviewers per case, retained input-first judgments, disagreements and final resolutions. Source, truth, oracle, profile and review hashes are bound into the manifest.
- [Pinned selection evidence](scripts/engine_eval/selection_evidence.json): 24 offline OCR review/delegate previews across all 12 PR fixtures, with explicit exceptions for injected unavailable evidence. Scope comparison uses canonical path sets; export orders assignments lexicographically.
- [Offline preparation/export](scripts/engine_eval/prepare.py) and [runnable checks](scripts/engine_eval/test_pilot.py): no application DB or provider access, no new dependencies. Python 3.11+ and Git are sufficient.

Each case has `input.json`, `truth.json` and `oracle.py` under `scripts/engine_eval/pilot/<case_id>/`. Inputs contain original synthetic source and legitimate requirements. Truth contains the defect proof, current finding locations and a fixed/negative control. Only source repositories, neutral task context and diffs are exported; truth, reviewer records, controls and oracles stay with the scorer. Exported repositories include excluded synthetic inputs so the production selection adapter can be tested; those bytes must still be withheld from model context according to the workload's policy.

The manifest is the authoritative digest. A changed source, label, oracle, profile or review record changes that identity. Stale peer/adjudication hashes fail preparation even with `--freeze`. Refreezing is an explicit maintenance operation after re-review, never a way to conceal changed evidence.

## Case inventory

There are 17 quality microcases (13 defects: nine high/four medium; four clean) and three incomplete-evidence reliability fixtures, totaling 12 PR/eight security cases. Controls are not extra independent cases. Every head/control oracle pair is checked; a passing structural oracle does **not** turn an incomplete case into a clean one. The three reliability fixtures are excluded from quality precision/recall denominators and scored for honest coverage/failure behavior separately.

| Case | Workload and boundary | Expected |
| --- | --- | --- |
| pr-01 | Round invoice tax once across all items | Defect, medium |
| pr-02 | Repository return contract consumed by unchanged API; insertion precedes failure | Defect, high |
| pr-03 | Expiration timestamps with explicit timezone offsets | Defect, high |
| pr-04 | Inclusive selected-file maximum | Defect, medium |
| pr-05 | Renamed module, deleted old path, stale caller import | Defect, medium |
| pr-06 | Generator-preserving display-name refactor | Clean |
| pr-07 | Ambiguous payment timeout and stable idempotency key | Defect, high |
| pr-08 | Malformed/partial result must not count as completed clean coverage | Defect, high |
| pr-09 | Reset-token subject binding in eligible `auth.py` source | Defect, high |
| pr-10 | Documentation and TOML agree on updated concurrency | Clean |
| pr-11 | Eligible source plus excluded generated/credential-name examples | Clean |
| pr-12 | Required worker source/patch unavailable | Incomplete |
| sec-01 | SQLite literal search versus injected SQL syntax | Defect, medium |
| sec-02 | Tenant-bound invoice access, with positive reads for both tenants | Defect, high |
| sec-03 | Download destination stays within tenant root | Defect, high |
| sec-04 | Archive destination planning stays within restore root | Defect, high |
| sec-05 | Restricted-data infrastructure policy cannot grant public access | Defect, high |
| sec-06 | Parameterized query plus tenant isolation | Clean |
| sec-07 | Simulated stale advisory data; lockfile remains scanner input | Incomplete |
| sec-08 | Inventory contains no supported security target | Incomplete |

These are small Python/configuration fixtures, with explicit requirements. They establish reproducible feasibility inputs, not representative production quality. Path/archive cases prove planning violations without extracting an archive or accessing arbitrary files. Infrastructure checks inspect emitted policy, not a deployed cloud account. Scanner freshness/unsupported cases use simulated reports; there is no claim of live Trivy vulnerability detection. `pr-12` deliberately injects an unavailable assignment that the materialized Git diff cannot select by itself; route it through failure injection, not a normal equal-scope model comparison. `sec-07` retains its lockfile in scanner inventory but excludes it from semantic AI assignments. The manifest records those inventories separately. Rename scope in `pr-05` assigns only the new path and changed caller, preserving the old path as base evidence.

Two GPT-6.1 Sol high agents reviewed each case without authoring it and saved judgments before opening its truth/oracle. The parent resolved four severity calibrations and strengthened the tenant positive-read proof. Scope-only amendments for the renamed path and scanner-only lockfile were re-reviewed, retaining original drafts and amendment hashes. Reviewers share a model family; this is not human or external expert adjudication. Initial records and final review reports remain separately saved, including disagreements.

All cases and their controls belong to the candidate pilot split. No development or broader release corpus is fabricated by this delivery. The files are visible to implementers, so this is not a sealed held-out study: do not tune prompts/heuristics on its answers, and replace contaminated cases before a blind quality claim. M5 still needs real historical defects, larger changes, other languages, critical cases where actually demonstrated, graph-on/gap scenarios, fresh advisory coverage and representative workload sizes. Preserve conservative family grouping when adding related cases; unique IDs alone do not prove statistical independence.

## Owner choices and execution profile

The owner selected **OpenCode Go / `muse-spark-1.3-contributor`**, **“no limits”**, and **the current local Docker environment**. Protocol v1.1 records uncapped aggregate dollars/tokens/requests explicitly; it does not treat an omitted budget as permission. Ledger accounting, deadlines, finite retries and bounded concurrency remain required. The prepared profile keeps live execution disabled because this task prepares the experiment.

The shared connection uses the documented Responses endpoint, `https://opencode.ai/inference/go/openai/v1`. The repository's existing OpenCode preset points to Zen, so use an explicit Go API connection; do not substitute Zen or native authentication. Public rates observed on November 7 are USD 0.10 input / 0.20 output / 0.002 cached-read per million tokens. Go allowance usage and actual invoice charges are separate measurements. Contributor data can be used for training and is not covered by zero retention; this pilot contains synthetic data only. [Provider documentation](https://opencode.ai/docs/console/go/).

Unspecified model reasoning parameters are omitted, preserving provider defaults; review effort is `medium`, sensitivity `balanced`, language English, and graph is off for equal-context comparison. Model alias stability, returned version, supported options, context/output limits and actual usage reporting require an authenticated M2 probe. No fallback is allowed if the selected model is unavailable.

The selected machine was reobserved: Docker 29.8.2, Linux aarch64/cgroup v2, 12 CPUs, 5,227,761,664 bytes RAM; host Darwin arm64. The profile specifies sequential arms/cases, PR concurrency two, security concurrency four, connection-wide concurrency four. Analysis is bounded to four CPUs/2 GiB, scanner to two CPUs/1 GiB without analysis overlap, with separate DB/Redis ceilings, PID/disk/tmp bounds, ten-minute requests and thirty-minute cases. These are selected conditions, **not yet enforced benchmark measurements**. Before measuring, use disposable persistence, enforce the profile and quiesce other Docker workloads. No development services were stopped to prepare it.

The pilot has 40 arm/case attempts (20 cases × two engines × one paired repetition); that is not a prediction or cap on model requests. Broader evaluation, repeated runs, warm-cache and overlapping-work tests remain separate. The incumbent image/toolchain is pinned; Mortis image/schema/task/rule hashes cannot be frozen until implementation exists. Git 2.39.5's existing OCR compatibility warning remains a preflight item.

## Reproduce and export

From the repository root:

```sh
python3 scripts/engine_eval/prepare.py
python3 -m unittest discover -s scripts/engine_eval -p 'test_*.py' -v
backend/.venv/bin/ruff check scripts/engine_eval
python3 scripts/engine_eval/prepare.py --export /private/tmp/mortis-engine-pilot-v1
```

Export requires a new destination and refuses to overwrite existing work. It creates 20 local Git repositories with neutral commit messages and deterministic timestamps; no remotes are configured. The preparer never executes fixture source. The tests intentionally execute the trusted, authored oracle/source strings locally using only the standard library; this is not a sandbox for arbitrary repositories.

Verified: six offline tests covering all 40 head/control outcomes; deterministic manifest/repository/diff reproduction; export source hashes and merge bases; truth separation; invalid paths/locations and stale adjudication rejection; Ruff. All 24 pinned OCR previews ran in a disposable network-none container with no credentials or inference; the 11 PR quality cases agree on selected sets, while `pr-12` records its injected-scope exception. No full application suite was rerun for these isolated fixture/tool/document changes. The earlier 1,200-case M0 baseline remains dated evidence in [ENGINE_M0.md](ENGINE_M0.md).

## What is still needed from the owner

**Nothing further is needed to begin offline M1.** Provider/model, aggregate budget policy and benchmark host are selected. Before the first live M2 probe or M5 pilot, identify the saved shared connection to bind (or create it in Settings), confirm the Go plan/remaining allowance and account eligibility for this model, and keep credentials in server-side storage. No API key belongs in this file or chat.

Engineering must still implement and verify the production-path harness, live protocol/options/usage behavior, effective resource enforcement, fresh scanner data and implementation-version pins. Those are M2/M5 acceptance tasks, not missing owner preferences. M1 remains responsible for shared-only admission, stale-worker fencing, historical reads, neutral source helpers, leases/reservations and the cross-domain/migration tests identified in [M0 gaps](ENGINE_M0.md#8-gaps-and-phase-gates). No live comparison or engine quality/performance claim follows from closing M0.
