# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Stack

FastAPI, PostgreSQL, Celery/Redis, OCR. React, TypeScript, Vite, React Router, TanStack Query, Tailwind CSS, shadcn/ui. The user explicitly selected Tailwind and shadcn/ui.

## Users

An owner and explicitly allowed teammates using their own self-hosted instance to review GitHub pull requests.

## Product Purpose

Turn OCR and official-client reviews into actionable GitHub comments while preserving review history and the current resolution state of every finding. Scan repository default branches for security findings and inspect saved coverage, evidence, and fix verification in the same workspace.

## Operating Context

Users connect their own GitHub App, shared API credentials or personal ChatGPT and Claude Code subscriptions, enable repositories, and inspect automatic or manually started reviews. Resolving or reopening a linked thread in either GitHub or Code Mortis updates the other. Development uses localhost with a separate webhook relay and GitHub App; server execution continues without the user's computer. An external agent can also work through authenticated MCP endpoints after per-user browser consent, with reviews, security and indexed code authorized independently.

## Capabilities and Constraints

MIT open source. The committed delivery includes shared API connections and personal ChatGPT and Claude Code subscriptions for manual and automatic reviews. Personal automation requires the owner's explicit authorization per repository. Official clients manage subscription authentication in persistent private server profiles. GitHub is application identity. Review coverage, blocked accounts and publication failures remain visible. Findings are never silently marked fixed merely because they disappear from a later model response. No silent provider fallback is allowed. Security scans preserve immutable reports and distinguish coverage, freshness, grade, assessment, and current finding resolution. GitHub issue publication requires reviewing the exact draft. Analytics uses saved accessible history and reported consumption, with missing reports kept explicit. Authenticated Reviews, Security and Code MCP endpoints are independently off by default and require per-user browser consent. Code exposes only read operations over selected opted-in repositories; management tools remain separately authorized on Reviews/Security. Public or destructive actions require exact-argument approval.

Members can issue named, expiring personal API tokens in Settings for Noodle and scripts. Each token is limited to explicit permissions, selected repositories, and the owner's current GitHub access. Token management, credentials, administration, MCP consent, and public security-issue publication stay in the browser. Revocation and expiry stop future token-origin execution while preserving saved results and publication history.

Repository code graphs are an independent, default-off setting in Project context. Administrators can enable default-branch indexing, inspect readiness, retry and cancel it. Indexing uses no model tokens or embeddings. Definitions use the tested language adapters; resolved file imports are limited to conservative Python and JavaScript/TypeScript relative paths. Callers and references are unsupported. Internal retrieval is revision-specific and bounded. New ChatGPT and Claude Code review runs can use bounded related source when an exact-commit graph is available. A missing index is queued without delaying the review. Additional review context uses the selected model's tokens; no savings are promised. The completed scoped Phase 2 evaluation did not establish benefit, so enrichment remains experimental and opt-in. All three connection kinds can use related-file hints during assessment and fix verification, with source loaded through the existing evidence request loop. Initial OCR/API analysis records an unsupported-adapter omission. Separate Code MCP tools provide revision-specific definitions, literal search, source, file imports and potential change impact without running models or rebuilding indexes. Source is untrusted and bounded; missing snapshots never substitute older code. Existing token/MCP-origin reviews and historical runs keep their original behavior. See docs/code-graph-phase3.md for validation boundaries, including external-client consent and evidence.

The pull-request **Code** tab starts with a bounded diff of the last-synced PR merge base to head, independently of graph enablement, readiness or supported languages. Shared file search and ten-file pages, saved summary groups and exact-revision occurrence findings help reviewers inspect changes. Groups reuse completed summaries only when revisions and input exclusions are compatible; they are file-level explanations, not generated line anchors. Findings retain immutable evidence and separate current resolution; older or unverified locations stay unanchored. Reading Code never starts a model, review or index. Dependencies, Definitions and retained Source are optional graph views with their existing repository opt-in and explicit administrator indexing. Missing patches, display limits, unavailable indexes and lost access remain explicit. Legacy Impact links retain their revision/path meaning. See docs/pr-code.md for bounds and verification.

## Brand Commitments

Code Mortis. Workflow-focused first release; distinctive mascot and branding work are deferred. English interface, configurable review-comment language.

## Evidence on Hand

The user authorized wilfredinni/noodle as an integration test repository and reported having some provider accounts. See docs/acceptance.md for local and external evidence; simulated tests do not establish real integration. Demonstration data must be clearly labeled.

## Product Principles

- Preserve the original review and explain current status separately.
- Reuse OCR and native GitHub capabilities.
- Make failures actionable and never call incomplete coverage clean.
- Limit each user's access to their current GitHub permissions.

## Accessibility & Inclusion

Keyboard-accessible controls, visible focus, sufficient contrast, responsive layout, and system/light/dark appearance.
