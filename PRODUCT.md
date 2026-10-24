# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Stack

FastAPI, PostgreSQL, Celery/Redis, OCR. React, TypeScript, Vite, React Router, TanStack Query, Tailwind CSS, shadcn/ui. The user explicitly selected Tailwind and shadcn/ui.

## Users

An owner and explicitly allowed teammates using their own self-hosted instance to review GitHub pull requests.

## Product Purpose

Turn OCR reviews into actionable GitHub comments while preserving review history and the current resolution state of every finding.

## Operating Context

Users connect their own GitHub App, shared API credentials or personal ChatGPT and Claude Code subscriptions, enable repositories, and inspect automatic or manually started reviews. Resolving or reopening a linked thread in either GitHub or Code Mortis updates the other. Development uses localhost with a separate webhook relay and GitHub App; server execution continues without the user's computer. An external agent can also work through authenticated MCP endpoints after per-user browser consent, with reviews and security authorized independently.

## Capabilities and Constraints

MIT open source. The committed delivery includes shared API connections and personal ChatGPT and Claude Code subscriptions for manual and automatic reviews. Personal automation requires the owner's explicit authorization per repository. Official clients manage subscription authentication in persistent private server profiles. GitHub is application identity. Review coverage, blocked accounts and publication failures remain visible. Findings are never silently marked fixed merely because they disappear from a later model response. No silent provider fallback is allowed.

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
