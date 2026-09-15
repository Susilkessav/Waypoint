# Submission feature inventory

The submission implements the core discovery → compile → review → replay workflow, including
real same-session human handoff, typed outcomes and irreversible reconciliation. The local
fixture is the validated application. Desktop and hosted infrastructure are deliberate cuts.

| Area | Delivered behavior | Evidence or regression coverage |
|---|---|---|
| Live discovery | Structured goal-driven exploration, policy checks, compile feedback | Two [genuine live recordings](evidence/README.md) |
| Compilation and approval | Typed references, validated checkpoints, stable versions and content-bound gates | `tests/test_discovery.py`, `test_submission_regressions.py`, `test_approval_queue.py` |
| Deterministic replay | Semantic/anchored targets, identity checks, four result statuses | Replay showcases and browser tests |
| Outcomes and recovery | Missing member, permission denial, bounded notice/session recovery, hard failure | `capabilities/lookup_member_balance/cases.yaml`, full generated-case tests |
| Human replay handoff | Lease take/return, same live session, redacted action log, verified return | `showcase-handoff-ambiguous`, `tests/test_handoff.py` |
| Discovery demonstration | Supported operator clicks compile into reusable steps; gaps block approval | [Demonstration and reuse](evidence/features/README.md), `tests/test_discovery_handoff.py` |
| Irreversible reconciliation | Durable scoped intents, authoritative probes, adoption without duplicate submission | Lost-response showcase, `tests/test_irreversible.py`, scope regressions |
| Tenant reuse | Delivered 1.4.0 riverbank override, per-variant approval and confidence | [Changed tenant](evidence/features/README.md), `tests/test_variants.py` |
| Catalog | Approved tools, Anthropic/OpenAI schemas, variant selection, invocation and handoff | Fresh agent-demo regression, catalog/console demonstration |
| Stability | Stored case verdicts, consistency, per-step drift/timing, confidence gates | `tests/test_stability.py`, `test_ledger.py`, `test_feature_boundaries.py` |
| Assisted relocation | One attempted safe-step choice, checked cassette, proposed draft | Saved live cassette, [reproduced assist](evidence/features/README.md), rejection/budget tests |
| Generated pytest | Full case set including injections; approved confidence-gated artifacts | `tests/test_codegen.py` executes complete generated files |
| Crash recovery | Atomic source claim, linked successor, safe-prefix reconstruction | [Actual worker exit and resume](evidence/features/README.md), resume/concurrency tests |
| Operator console | Shared-store control actions with Host/Origin/CSRF protection | Console tests and catalog-to-console handoff |
| Credentials | Authorized field injection, environment/keychain providers, sanitized failure | Secret-provider tests; real OS backend provisioning remains unverified |
| Submission materials | Reproducible presenter, shortened seven-section report, evidence and checklist | [SUBMISSION.md](SUBMISSION.md) |

## Artifact lineage

| Artifact | Purpose |
|---|---|
| lookup 1.0.0 | Original handwritten foundation; the recording also creates a separate isolated 1.0.0 draft. |
| lookup 1.1.0 | Retained live-discovered draft; provenance is preserved. |
| lookup 1.2.0 | Reviewed lookup with business outcomes and recovery; core showcase release. |
| lookup 1.3.0 | Reviewed confidence bar and optional assisted relocation; original live assist release. |
| lookup 1.4.0 | 1.3.0 plus the reviewed riverbank control override; base behavior is unchanged. |
| open_sub_account 1.0.0 | Live-derived workflow hardened with reviewed reconciliation. |

Adding an override changes the reviewed content, so 1.4.0 has fresh base and riverbank approvals.
Its confidence history starts empty. The fresh agent demo measures the current release before
advertising it; it does not bypass the catalog's gate. The small demonstration threshold is not
presented as a production reliability recommendation.

## Deliberate limits

Working Chromium execution does not imply desktop support. The desktop adapter is a stub and
extension design. There is no hosted multi-user service or remote browser-control channel.
Resume requires an operator to know the old process is gone. Reconciliation depends on the UI's
authoritative data and timestamp precision. Redaction is rule-based, not exhaustive detection.
Generated page objects lack the engine's execution guarantees. Keychain tests use fake providers.

Milestones A and B are implemented. The retained C extensions are demonstrated above; C is no
longer described as wholly skipped. D is the final verification and packaging recorded in
[SUBMISSION.md](SUBMISSION.md). [PROJECT_REVIEW.md](PROJECT_REVIEW.md) records the resolution of
the eight review findings. PLAN.md remains historical and is excluded from the submission.
