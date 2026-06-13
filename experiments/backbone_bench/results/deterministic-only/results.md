# Judge bench — backbone: `deterministic only`

| defect | tier | expected check | caught |
|--------|------|----------------|--------|
| missing-page | deterministic | `bundle-schema` | 1/1 |
| unwritten-leaderboard-key | deterministic | `bundle-schema` | 1/1 |
| no-daily-cap | deterministic | `daily-submission-cap` | 1/1 |
| short-dev-phase | deterministic | `dev-phase-duration` | 1/1 |
| no-sorting | deterministic | `leaderboard-sorting` | 1/1 |
| final-unlimited | deterministic | `final-phase-submission-limit` | 1/1 |
| kit-missing | deterministic | `starting-kit` | 1/1 |
| single-phase | deterministic | `two-phase-structure` | 1/1 |
| docker-unpinned | deterministic | `docker-image-pinned` | 1/1 |
| caps-contradiction | judged | `judged-docs-config-consistency` | skipped |
| metric-direction-contradiction | judged | `judged-docs-config-consistency` | skipped |
| phase-dates-contradiction | judged | `judged-docs-config-consistency` | skipped |
