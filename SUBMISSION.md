# Final submission

Submission repository: [Susilkessav/Waypoint](https://github.com/Susilkessav/Waypoint).
Verified public, with `main` as its default branch.

## Reviewer entry points

1. [README.md](README.md): setup and the exact discovery → review → approval → replay path.
2. [REPORT.md](REPORT.md): approximately 1,100 words under the seven requested design headings.
3. [evidence/README.md](evidence/README.md): genuine live-model recordings and verified showcases.
4. [PROJECT_STATUS.md](PROJECT_STATUS.md): delivered features, artifact lineage and deliberate cuts.
5. [docs/demo-script.md](docs/demo-script.md): narration matching the Enter-driven presenter.

A video is optional. The presenter is `docs/present.sh`; the automated reproduction needs no
API key. Desktop execution and hosted infrastructure are explicitly deferred. The OS keychain
adapter is tested with fake providers, with no claim of live backend provisioning.

## Final verification

Verified on 15 September 2026:

| Check | Result |
|---|---|
| Ruff and whitespace validation | Passed |
| mypy | Passed, 54 source files |
| Project non-live-model suite | 549 passed |
| Full suite in a clean source export, separate locked environment, no API key | 550 passed in 570.72 seconds |
| Final resume and migration checks, including the added prefix-handoff regression | 12 passed |
| Complete generated regression files | Passed for 1.2.0 and confidence-gated 1.3.0, including injected cases |
| Fresh agent-demo entry point | Passed inside the suite; private ledger measured before invocation |
| Core evidence generation | All nine scenarios produced their declared results |
| Retained feature demonstrations | All ten evidence groups completed; includes actual worker exit and successful resume |
| Recording flow from clean state | Passed; manual handoffs separately covered by the showcase and feature runs |
| Locked clean install | Passed using cached dependencies; no pre-existing environment or state |
| Document links and evidence redaction scan | Passed; sampled screenshots visually inspected |
| Submission repository | Verified public; default branch `main` |

The extra concurrency check was added after the first full run; the final prefix-handoff
regression was added after clean-suite collection and passed in the focused run. The repository
now contains 551 tests. The final resume implementation uses the ordinary driver during safe
prefix reconstruction, preserving checkpoint checks, progress updates, recovery and handoff.

See [verification records](evidence/verification/README.md). The clean export contained the
intended tracked and non-ignored source files, with no `.env`, runtime database or existing
virtual environment. This was a local clean-package rehearsal, not a claim that the final
submission commit has already been published.

## Included materials

- Source, tests, lockfile and CI configuration.
- Versioned capabilities, including the 1.4.0 base/riverbank fixture release.
- Two original live discovery recordings, original live agent/assist recordings, regenerated
  replay showcases and retained feature demonstrations.
- README, the seven-section report, rules, current inventory, review resolutions and demo guide.

Real `.env` files, local SQLite state, environments, caches and historical review scratch output
are excluded. The old PLAN.md is historical context and is not a deliverable.

## Publishing and sending

The prepared local revision must be present on the repository's public submission branch before
the link is sent. Verify the latest commit and that README links work from a fresh checkout.
This preparation does not send an email or publish changes automatically.

The assessment asks for the public repository URL on its own line in the submission email,
sent from the application email address to `assignments@interface.ai`. Send the repository link,
not a zip archive. Add a video link only if you record one. These are the assessment's delivery
instructions; no message has been sent on your behalf.
