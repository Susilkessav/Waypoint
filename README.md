# Waypoint

**Record-once, replay-many automation for legacy back-office UIs that have no API.**

An LLM discovers a path through a legacy UI once. That path is compiled into a typed,
versioned **capability**. Production runs replay the capability deterministically, with no
model deciding anything. When replay meets a state it doesn't recognize, control transfers
to a human on the same live session, and what the human does is recorded as evidence.

Built for the interface.ai take-home (Computer-Use Automation System).

---

## ⚠️ Status: design complete, implementation not started

**Nothing in the "Usage" section below runs yet.** Every command is marked as `PLANNED`
until the milestone that implements it lands. This section is updated as tasks complete —
it is deliberately not written in the future-perfect tense that makes a repo look finished.

| Component | Status | Milestone |
|---|---|---|
| Design and execution rules | ✅ complete — [PLAN.md](PLAN.md) §6 | — |
| Repo scaffold, CI, test harness | ✅ complete | A1 |
| Target app (hostile legacy fixture) | ✅ complete | A2 |
| Surface port, AX perception, sensitivity classifier | ⬜ not started | A3 |
| Locator ladder, policy engine, redactor | ⬜ not started | A4 |
| Artifact schema, replay engine, approval | ⬜ not started | A5 |
| Discovery loop (real LLM), compiler | ⬜ not started | A6 |
| Control lease, escalation, live handoff | ⬜ not started | A7 |
| Reconciliation, recovery, outcomes | ⬜ not started | B |
| Tenant overrides, capability catalog | ⬜ conditional | C |

`PLAN.md` is a working document and is not part of the submission (it is gitignored).
The design write-up is [REPORT.md](REPORT.md).

---

## What this is meant to demonstrate

Banks and credit unions run a long tail of back-office applications with no API — core
banking screens, servicing tools, admin consoles — where the only way in is to drive the UI
the way a human operator would. Waypoint is the integration layer that lets an AI agent do
that **reliably and cheaply**, by using a model to figure the flow out once and then never
again.

The design is built around one safety posture, which the rules in `PLAN.md` §6 all follow
from:

> **The system never acts on the absence of evidence.**
> Not finding a confirmation is not proof the operation didn't happen.
> Not finding a row is not permission to click whatever occupies its old position.
> Not finding a mismatch is not proof the right member is on screen.

---

## Planned usage

> `PLANNED` — not yet implemented.

### Setup

```bash
make install                  # uv sync --locked --extra dev  +  playwright chromium
cp .env.example .env          # then fill in ANTHROPIC_API_KEY
```

`uv.lock` is committed, and both `make install` and CI run `uv sync --locked` — which
fails rather than silently re-resolving if the lock is stale. Dependencies cannot drift
without a change to this repository. Use `make lock` to update them deliberately.

`ANTHROPIC_API_KEY` is required **only** for a live discovery run. Everything else —
replay, error handling, escalation, the full demo — runs without it.

### Running without live services

Discovery decisions are recorded to a **cassette** keyed by snapshot hash, so a recorded
LLM run can be reproduced offline with no API key and no network:

```bash
waypoint discover --capability-id lookup_member_balance --llm cassette   # PLANNED
```

### Demo

Three terminals; the first two block.

```bash
# terminal 1
make app                                       # target app on :8080          PLANNED

# terminal 2 (only while demonstrating the handoff)
watch -n 2 'waypoint intervene list'           #                              PLANNED
```

```bash
# terminal 3                                                                  PLANNED
waypoint discover --capability-id lookup_member_balance \
  --goal "Look up member {{member_id}} and read their current savings balance" \
  --entry http://localhost:8080/console \
  --bind member_id=12345:string:internal \
  --expect-output savings_balance:money:pii

waypoint approve lookup_member_balance --version 1.0.0 --tenant base

waypoint replay lookup_member_balance --input member_id=12345   # success
waypoint replay lookup_member_balance --input member_id=67890   # generalizes to a new member
waypoint replay lookup_member_balance --input member_id=00000   # business_outcome, exit 0
waypoint replay lookup_member_balance --input member_id=12345 --inject interstitial
waypoint replay lookup_member_balance --input member_id=12345 --inject 500
```

The full ten-command path, including the irreversible capability and the live-session
handoff, is in [PLAN.md](PLAN.md) §11.

---

## Repository tour

| Path | What lives there |
|---|---|
| `waypoint/surface/` | The `Surface` port, CDP accessibility-tree perception, sensitivity classification, the locator ladder, and a desktop stub |
| `waypoint/policy/` | Allowlist and risk classification, the redactor, the secret broker |
| `waypoint/signatures/` | Declarative state recognizers, shared by discovery and replay |
| `waypoint/discovery/` | The LLM loop, prompts, transcript writer, cassette |
| `waypoint/compiler/` | Transcript → artifact |
| `waypoint/artifact/` | The capability schema and approval gates — the focal point |
| `waypoint/replay/` | The deterministic executor, recovery, reconciliation |
| `waypoint/session/` | Control lease, escalation, human action log, intent records |
| `waypoint/catalog/` | Approved-capability registry and invocation API |
| `target_app/` | The hostile legacy fixture. **Nothing in `waypoint/` imports it** — enforced by a test |
| `capabilities/` | Compiled, versioned capability artifacts |
| `evidence/` | Per-run logs, screenshots, accessibility snapshots, results |

---

## What is deliberately mocked or stubbed

Full reasoning in [REPORT.md](REPORT.md) §7.

- **Desktop surface** — the port is defined and `DesktopSurface` raises `NotImplementedError`
  with each method's UIA/AXAPI equivalent documented. The seam is real; the implementation is absent.
- **Operator console** — a CLI (`waypoint intervene`) rather than a web UI. It reads and writes
  the same tables a console would, so the control-transfer model is real and the UI is a shell.
- **Credential storage** — environment variables behind a `SecretBroker` interface that a real
  vault would drop into.
- **Process-death recovery** — the write-ahead intent record makes a crashed run *safe*
  (it forces human reconciliation before anything re-executes), but automatic browser
  reattachment is not built.
- **No queues, workers, or multi-tenant infrastructure** — the brief explicitly does not reward it.

---

## License

Unlicensed take-home submission.
