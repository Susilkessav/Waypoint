# Stability: lookup_member_balance 1.3.0

- **Verdict:** stable
- **Runs:** 16 (16 did what their case declared)
- **Confidence:** stable · score 0.6756 · 8 clean
- **Content hash:** `sha256:8658754c79ea0f70ad7b4f640a8856b5b2607faf19f803f8c5c984065311ba86`
- **Started:** 2026-09-15T04:36:17+00:00

| Case | Runs | As declared | Statuses | Same answer | Median | p95 | Recoveries |
|---|---|---|---|---|---|---|---|
| found | 2 | 2/2 | success ×2 | yes | 3767 ms | 3913 ms | 0 |
| another-member | 2 | 2/2 | success ×2 | yes | 3738 ms | 3802 ms | 0 |
| no-such-member | 2 | 2/2 | business_outcome ×2 | yes | 2049 ms | 2073 ms | 0 |
| not-authorized | 2 | 2/2 | business_outcome ×2 | yes | 2651 ms | 2660 ms | 0 |
| maintenance-notice | 2 | 2/2 | success ×2 | yes | 4474 ms | 4487 ms | 2 |
| session-expired | 2 | 2/2 | success ×2 | yes | 6440 ms | 6449 ms | 2 |
| server-error | 2 | 2/2 | failure ×2 | yes | 3129 ms | 3161 ms | 0 |
| wrong-member-shown | 2 | 2/2 | escalated ×2 | yes | 13307 ms | 13374 ms | 0 |
