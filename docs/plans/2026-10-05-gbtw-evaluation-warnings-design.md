# GBTW evaluation warnings design

Loop: full — hook enforcement result handling; two implementation/test files.

## Requirements
See docs/plans/2026-10-05-gbtw-evaluation-warnings-requirements.md. User explicitly requested a new loop for the already-established fix; continue through planning, execution and review without redundant approval.

## Design
Predicted answer: yes (delegated).
Reuse ic_queue_warning after background graders join. Each run_check failure returns result unavailable (not A), a bounded fixed failure reason and empty raw text. Valid grades remain unchanged. read_check_result distinguishes intentionally suppressed checks from missing/corrupt results of fired checks. Parent aggregates unavailable check labels, queues one non-blocking warning before grade decision branches, and uses an honest approval summary when checks are unavailable. A different valid D/F grade or deterministic gate retains authority to block; evaluation unavailability alone does not.

Existing warning queue is preferable to new stderr-only warnings: it delivers next-prompt context on both clients despite Codex stripping approval metadata. Adding Stop protocol fields would change a validated protocol without necessity. No retries, network calls or timeout changes are added. Warning persistence failures stay fail-open. Do not echo raw malformed LLM output into warnings.

## Root cause and verification
Source run_check maps empty responses and invalid grades to A; final approval always says all checks passed. Existing routing failure fixture proves approval but asserts no warning. Extend that fixture with failing warning/summary assertions before changing code. Existing ic_queue_warning writes only a session queue and ignores IO errors; ic_drain_warnings delivers additionalContext. Test real wrapper with isolated HOME/database and stub transport for both clients; confirm queue drain, no success/suppression warnings, and failure with unwritable queue. Retain routing, C-warning and wrapper regressions.

Predicted answer: Yes, invoke writing-plans (delegated).
