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

Users connect their own GitHub App and API credentials, enable repositories, and inspect automatic or manually started reviews. Resolving or reopening a linked thread in either GitHub or Code Mortis updates the other.

## Capabilities and Constraints

MIT open source. APIs first; personal subscription connections follow later. GitHub is application identity. Admin-managed shared API credentials power automation. Review coverage and publication failures must remain visible. Findings are never silently marked fixed merely because they disappear from a later model response.

## Brand Commitments

Code Mortis. Workflow-focused first release; distinctive mascot and branding work are deferred. English interface, configurable review-comment language.

## Evidence on Hand

No customer accounts, deployment, model credentials, or production review records are supplied. Demonstration data must be clearly labeled.

## Product Principles

- Preserve the original review and explain current status separately.
- Reuse OCR and native GitHub capabilities.
- Make failures actionable and never call incomplete coverage clean.
- Limit each user's access to their current GitHub permissions.

## Accessibility & Inclusion

Keyboard-accessible controls, visible focus, sufficient contrast, responsive layout, and system/light/dark appearance.
