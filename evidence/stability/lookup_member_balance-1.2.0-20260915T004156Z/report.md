# Stability: lookup_member_balance 1.2.0

- **Verdict:** stable
- **Runs:** 16 (16 did what their case declared)
- **Confidence:** stable · score 0.6756 · 8 clean
- **Content hash:** `sha256:c462a619f17e27b5df0d2cee8f4e4de4b530c5cc036ef9afeb84a5eb3fce41d9`
- **Started:** 2026-09-15T00:40:39+00:00

| Case | Runs | As declared | Statuses | Same answer | Median | p95 | Recoveries |
|---|---|---|---|---|---|---|---|
| found | 2 | 2/2 | success ×2 | yes | 3540 ms | 3587 ms | 0 |
| another-member | 2 | 2/2 | success ×2 | yes | 3524 ms | 3573 ms | 0 |
| no-such-member | 2 | 2/2 | business_outcome ×2 | yes | 2049 ms | 2108 ms | 0 |
| not-authorized | 2 | 2/2 | business_outcome ×2 | yes | 2517 ms | 2522 ms | 0 |
| maintenance-notice | 2 | 2/2 | success ×2 | yes | 4328 ms | 4331 ms | 2 |
| session-expired | 2 | 2/2 | success ×2 | yes | 6172 ms | 6189 ms | 2 |
| server-error | 2 | 2/2 | failure ×2 | yes | 2987 ms | 2996 ms | 0 |
| wrong-member-shown | 2 | 2/2 | escalated ×2 | yes | 13184 ms | 13261 ms | 0 |
