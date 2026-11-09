# Code Mortis documentation

Start with the [project README](../README.md) to run an instance. This directory contains product and developer guides, operational instructions, implementation plans, and dated verification records.

## Guides

| Document | Use it for |
| --- | --- |
| [Product](product.md) | Users, capabilities, constraints and product decisions. |
| [Design system](design.md) | UI components, typography, colors, layout and accessibility. |
| [Backend architecture](architecture.md) | Package layout, runtime entry points and dependency boundaries. |
| [Operations](operations.md) | Configuration, upgrades, backup, restoration and troubleshooting. |
| [Acceptance record](acceptance.md) | Verified behavior and integrations still awaiting live validation. |
| [PR Code](pr-code.md) | Code navigation, findings, revision handling and validation. |
| [API token route policy](api-token-route-policy.md) | Exact token permissions and browser-only operations. |

## Implementation plans

| Area | Plan and supporting records |
| --- | --- |
| Product delivery | [Product implementation plan](plans/product.md). |
| Mortis Engine | [Engine roadmap](plans/engine/plan.md), [evaluation protocol v1](plans/engine/evaluation-v1.md), [pilot corpus](plans/engine/pilot.md), and the milestone records below. |
| Code graph | [Implementation plan](plans/code-graph/plan.md), [Phase 0](plans/code-graph/phase-0.md), [Phases 1D–1E](plans/code-graph/phase-1d-1e.md), [Phase 2](plans/code-graph/phase-2.md), [Phase 3](plans/code-graph/phase-3.md), and [Phase 4](plans/code-graph/phase-4.md). |
| PR Code | [Implementation plan](plans/pr-code/plan.md), [navigation performance](plans/pr-code/performance.md), and [review workflow](plans/pr-code/review-workflow.md). |
| Personal API tokens | [Implementation plan](plans/api-tokens.md). |
| MCP | [Implementation plan](plans/mcp.md). |

## Engine milestones

The [engine roadmap](plans/engine/plan.md) records the current decisions and checklist. Milestone reports preserve the source versions, verification, limitations and handoffs recorded during implementation.

| Milestone | Record |
| --- | --- |
| M0 | [Source audit, feature inventory and evaluation preparation](plans/engine/m0.md). |
| M1 | [Foundation, durable tasks and shared-only connections](plans/engine/m1.md). |
| M2 | [Provider transports and admission](plans/engine/m2.md). |
| M3 | [PR analysis and code graph](plans/engine/m3.md). |
| M4 | [Complete workflows and security](plans/engine/m4.md). |
| M5 and personal adoption | [Evaluation, reliability evidence, remaining gaps and OCR retirement](plans/engine/m5.md). |

## Reading the records

- Plans and phase reports contain dated decisions and historical results. Read the current roadmap and acceptance record before treating an older statement as current behavior.
- Paths shown as inline code and shell commands are relative to the repository root unless the document states otherwise. Clickable relative links resolve from the document containing them.
- Frozen artifacts in `data/` and retained files in `docs/evidence/` keep their original paths, hashes and contents. Historical artifact inventories can therefore contain document names from before this reorganization.
- The [Noodle collection README](../collections/code-mortis/README.md) stays beside its runnable collection. The root [agent instructions](../AGENTS.md), [license](../LICENSE) and [third-party notices](../THIRD_PARTY_NOTICES.md) stay in their conventional discovery locations.
