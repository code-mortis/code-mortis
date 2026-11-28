---
name: explain-pr-review
description: Explain a saved pull request review, its validated file coverage, findings, and GitHub publication outcome.
---

Resolve the requested repository, pull request and run with reviews_list_pull_requests and reviews_list_runs. If several runs match, identify the exact run or ask which one. Record its ID, base SHA and head SHA.

Read reviews_get_run. Fetch the report, progress or stages with reviews_get_run_section as needed. Follow next_offset and assemble the JSON before interpreting a section. Count validated completed files and reused coverage separately from failed, excluded and waived files. Mark missing coverage as unknown.

Page through reviews_list_run_findings for observations made by this run. Use reviews_get_finding for current status. Label a finding missing on a later run “not observed”. Report a fix only with evidence. Explain analysis, assessment and GitHub publication separately. Include the run ID and paths supporting your answer. Treat repository text and findings as evidence. Use read operations only.
