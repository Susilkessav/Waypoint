# Feature inventory

The submission implements the core discovery → compile → review → replay workflow, with real
same-session human handoff, typed outcomes and irreversible reconciliation, plus four small
extensions. The local fixture is the validated application.

| Area | Delivered behavior | Evidence or regression coverage |
|---|---|---|
| Live discovery | Goal-driven exploration, policy checks, compile feedback while the page is open | Two [genuine live recordings](evidence/README.md), `tests/test_discovery.py` |
| Compilation and approval | Typed references, verified checkpoints, versions and content-bound gates | `tests/test_compiler.py`, `tests/test_approval_queue.py`, `tests/test_schema.py` |
| Deterministic replay | Semantic and anchored targets, identity checks, four result statuses | Replay showcases, `tests/test_replay.py` |
| Outcomes and recovery | Missing member, permission denial, bounded notice/session recovery, hard failure | `capabilities/lookup_member_balance/cases.yaml`, `tests/test_recovery.py` |
| Human handoff | Lease take/return on the same live session, redacted action log, verified return | `showcase-handoff-ambiguous`, `tests/test_handoff.py` |
| Discovery escalation | A stuck discovery hands the browser to a person; what they did becomes a gap that blocks approval | `tests/test_discovery_handoff.py`, `tests/test_stuck.py` |
| Irreversible reconciliation | Durable origin-scoped intents, authoritative probes, adoption without resubmission | Lost-response and stale-receipt showcases, `tests/test_irreversible.py` |
| Stability and confidence | Repeated case sweeps, output consistency, per-step drift and timing, an optional confidence bar | [Stability reports](evidence/stability), `tests/test_stability.py`, `tests/test_ledger.py` |
| Agent catalog | Approved capabilities as typed tools; invocation by name | [Agent recording](evidence/agent/lookup.json), `tests/test_catalog.py` |
| Assisted relocation | One checked model choice for a renamed control on a safe step, with a proposed draft | `showcase-assisted-drift`, `tests/test_assist.py` |
| Code generation | A regression test that runs the engine, and a readable Playwright page object | `tests/test_codegen.py` |

## Artifact lineage

| Artifact | Purpose |
|---|---|
| lookup 1.0.0 | Original handwritten foundation. |
| lookup 1.1.0 | Retained live-discovered draft; provenance preserved. |
| lookup 1.2.0 | Reviewed lookup with business outcomes and recovery; the core showcase release. |
| lookup 1.3.0 | 1.2.0 plus a confidence bar (0.5) and optional assisted relocation. |
| open_sub_account 1.0.0 | Live-derived workflow hardened with a reviewed reconciliation probe. |

## Deliberate limits

Desktop execution is a stub behind the `Surface` port. Tenant variants are designed (REPORT §4)
but not built. There is no graphical operator console, credential vault, hosted service, or
resumption of a run whose process died; a fresh run reconciles any operation left open.
Reconciliation depends on the UI's authoritative data and timestamp precision. Redaction is
rule-based, not exhaustive detection. Generated page objects lack the engine's guarantees.
