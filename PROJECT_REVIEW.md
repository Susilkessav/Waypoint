# Resolution of the submission review

The 15 September review identified eight feature-integration findings. The implementation now
addresses each one, with focused regression coverage and demonstrations of the retained
extensions. Final check results are recorded in [SUBMISSION.md](SUBMISSION.md).

| Finding | Resolution | Proof |
|---|---|---|
| **F1 · Cross-tenant intents** | Persist normalized origin + variant and the reviewed contract hash. Other scopes are excluded; legacy or changed contracts require operator reconciliation. | Independent origin/variant tests, legacy/contract-change refusal, existing lost-response and crash reconciliation tests. |
| **F2 · Confidence ignores failed cases** | Persist evaluated case verdict/reason. Unfinished measurements, wrong outcomes and inconsistent outputs cannot earn passing confidence. | Broken-sweep-to-unattended-gate regression; output-consistency regression; ledger and stability tests. |
| **F3 · Generated cases lose semantics** | Carry fixture injections; measure with confidence enforcement disabled while retaining approval/policy. Missing case files receive a clear CLI refusal. | Complete generated case files run against the fixture for 1.2.0 and confidence-gated 1.3.0. |
| **F4 · Assist counts only acceptance** | Consume an attempt before entering the provider. Record rejected choices and provider failures separately from accepted relocations. | Repeated rejection/provider-failure tests prove a single call; accepted relocation remains covered. |
| **F5 · Stale or ambiguous assist cassette** | Match normalized sanitized screen hash and require one role/name match; retain deterministic validation afterward. | Changed-screen and duplicate-target refusal; successful playback of the existing genuine assist cassette. |
| **F6 · Fresh agent demo unavailable** | Start private state, measure declared lookup cases in that exact ledger, then offer the tool. Mismatches and unavailable tools stop clearly. | Full fresh-directory, no-key subprocess test; retained agent recording remains unchanged. |
| **F7 · Original crash run reusable** | Atomically retire the source and create a linked successor before execution. Verify identity and reconstruct only a safe prefix. | Concurrent single-winner claim, successful real-browser resume, duplicate-source refusal, actual worker-exit demo. |
| **F8 · Console cross-origin mutation** | Validate local Host and same Origin, require session CSRF token, use local redirects and bounded lease durations. | Take/return/abort/reconcile rejection tests plus legitimate console-to-engine handoff. |

The main focused regressions are in [test_feature_boundaries.py](tests/test_feature_boundaries.py),
with coverage beside each feature. Existing reconciliation fixtures now create scoped intents;
a separate migration case preserves conservative refusal of old unscoped records.

Additional integration work completes the reviewed feature paths: lookup 1.4.0 includes a real
riverbank override, catalog commands expose variants and handoff, finished catalog escalations
no longer claim a person is actively waiting, and keychain-provider failures are sanitized.
The recording presenter uses isolated state and the same short helper commands as the README.

The original review's failed probes and baseline logs remain local historical material under
`reviews/`; they are excluded from the submission. They are not the current test suite or
feature inventory. [PROJECT_STATUS.md](PROJECT_STATUS.md) is the current inventory.
