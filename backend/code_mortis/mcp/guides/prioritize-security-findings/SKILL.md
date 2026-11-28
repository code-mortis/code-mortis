---
name: prioritize-security-findings
description: Rank findings from a saved repository security scan using evidence, assessment, history, and coverage.
---

Identify the repository, exact scan ID and pinned commit with security_list_scans and security_get_scan. Page through security_list_report_findings for that scan. Use security_list_assessments and security_get_assessment for Mortis conclusions. Read security_get_finding_observations for history and security_get_finding for current state.

Use security_get_scan_coverage to check inventory, excluded paths, units and problems. Distinguish deterministic scanner observations from model assessments. Keep uncertain or withheld assessments explicit, and never infer that an excluded path is clean.

Return a ranked investigation list. For each item cite the finding ID and path, evidence, confidence, reason for priority, and one defensive next step. Read saved redacted evidence only. Keep triage changes and GitHub publication separate from this investigation.
