# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Stack

FastAPI, PostgreSQL, Celery/Redis, Mortis Engine and Trivy. React, TypeScript, Vite, React Router, TanStack Query, Tailwind CSS, shadcn/ui. The user explicitly selected Tailwind and shadcn/ui.

## Users

An owner and explicitly allowed teammates using their own self-hosted instance to review GitHub pull requests.

## Product Purpose

Turn Mortis Engine reviews into actionable GitHub comments while preserving review history and the current resolution state of every finding. Scan repository default branches for security findings and inspect saved coverage, evidence, and fix verification in the same workspace.

## Operating Context

Users connect their own GitHub App and shared API credentials, enable repositories, and inspect automatic or manually started Mortis reviews. Resolving or reopening a linked thread in either GitHub or Code Mortis updates the other. Development uses localhost with a separate webhook relay and GitHub App; server execution continues without the user's computer. An external agent can also work through authenticated MCP endpoints after per-user browser consent, with reviews, security and indexed code authorized independently.

## Capabilities and Constraints

Apache 2.0 open source. New PR reviews and security scans use Mortis Engine through shared API connections only. Saved results, findings, coverage, consumption and engine identities remain available for retired execution modes. An old saved execution is never interpreted as a Mortis plan. Repository settings migrate additively for future work, and historical inference cannot resume. Explicit summary regeneration creates new work with current shared settings while preserving the original review engine identity. The owner authorized personal early adoption on November 8, 2026; this does not approve missing or failed experimental gates. See docs/plans/engine/m5.md for retained evidence and uncertainty. GitHub is application identity. Review coverage, blocked accounts and publication failures remain visible. Findings are never silently marked fixed merely because they disappear from a later model response. No silent provider fallback is allowed. Security scans preserve immutable reports and distinguish coverage, freshness, grade, assessment, and current finding resolution. GitHub issue publication requires reviewing the exact draft. Analytics uses saved accessible history and reported consumption, with missing reports kept explicit. Authenticated Reviews, Security and Code MCP endpoints are independently off by default and require per-user browser consent. Code exposes only read operations over selected opted-in repositories; management tools remain separately authorized on Reviews/Security. Public or destructive actions require exact-argument approval.

Members can issue named, expiring personal API tokens in Settings for Noodle and scripts. Each token is limited to explicit permissions, selected repositories, and the owner's current GitHub access. Token management, credentials, administration, MCP consent, and public security-issue publication stay in the browser. Revocation and expiry stop future token-origin execution while preserving saved results and publication history.

Repository code graphs are an independent, default-off setting in Code graph configuration. Administrators can enable default-branch indexing, inspect readiness, retry and cancel it. Indexing uses no model tokens or embeddings. Definitions use the tested language adapters; resolved file imports are limited to conservative Python and JavaScript/TypeScript relative paths. Callers and references are unsupported. Internal retrieval is revision-specific and bounded. Eligible new Mortis PR reviews use the existing graph retrieval to include bounded related source from the exact reviewed commit. Required review source keeps priority; missing, incompatible or oversized optional context is recorded explicitly, and a missing index can be queued without delaying review. Saved plans freeze their attempted graph selection. Assessment and fix verification retain existing related-file discovery and evidence loading. Indexing, navigation, permissions, exclusions and source limits remain independent of review execution. Security scanning does not have this PR graph enrichment.

Additional graph context uses the selected model's tokens; no savings are promised. The historical native-client Phase 2 evaluation completed 18/18 results with 306,289 reported tokens but did not establish benefit; it does not measure the current Mortis implementation. Enrichment remains experimental and opt-in. Separate Code MCP tools provide revision-specific definitions, literal search, source, file imports and potential change impact without running models or rebuilding indexes. Source is untrusted and bounded; missing snapshots never substitute older code. Existing token/MCP eligibility checks remain in effect. See docs/plans/code-graph/phase-3.md and docs/plans/engine/m3.md for validation boundaries.

The pull-request **Code** tab starts with a bounded diff of the last-synced PR merge base to head, independently of graph enablement, readiness or supported languages. Shared file search and ten-file pages, saved summary groups and exact-revision occurrence findings help reviewers inspect changes. Groups reuse completed summaries only when revisions and input exclusions are compatible; they are file-level explanations, not generated line anchors. Findings retain immutable evidence and separate current resolution; older or unverified locations stay unanchored. Reading Code never starts a model, review or index. Dependencies, Definitions and retained Source are optional graph views with their existing repository opt-in and explicit administrator indexing. Missing patches, display limits, unavailable indexes and lost access remain explicit. Legacy Impact links retain their revision/path meaning. See docs/pr-code.md for bounds and verification.

## Brand Commitments

Code Mortis. Workflow-focused first release; distinctive mascot and branding work are deferred. English interface, configurable review-comment language.

## Evidence on Hand

The user authorized wilfredinni/noodle as an integration test repository and reported having some provider accounts. See docs/acceptance.md for local and external evidence; simulated tests do not establish real integration. Demonstration data must be clearly labeled.

## Product Principles

- Preserve the original review and explain current status separately.
- Keep engine code and prompts owned by this project, and reuse native GitHub capabilities.
- Make failures actionable and never call incomplete coverage clean.
- Limit each user's access to their current GitHub permissions.

## Accessibility & Inclusion

Keyboard-accessible controls, visible focus, sufficient contrast, responsive layout, and system/light/dark appearance.
