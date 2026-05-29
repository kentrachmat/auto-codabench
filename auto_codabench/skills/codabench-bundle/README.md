# codabench-bundle — provenance & maintenance

**Skill kind:** knowledge / reference (consulted by the Phase 2 driver).
**File:** [`SKILL.md`](./SKILL.md) (~850 lines).

## What it contains

Schema-level reference for generating a Codabench competition bundle on
disk:

| § | Section |
|---|---|
| 1  | Bundle directory layout (`competition.yaml` + assets) |
| 2  | `competition.yaml` top-level keys (required + optional) |
| 3  | Versioning (`version: 2`) |
| 4  | Pages (tabs shown in the UI) |
| 5  | Phases (active windows, sequential, non-overlapping) |
| 6  | Tasks (indexed, referenced by phases) |
| 7  | Scoring program contract (`metadata.yaml`, `/app/*` filesystem, `scores.json`) |
| 8  | Ingestion program contract (γ code-submission only) |
| 9  | `scores.json` ↔ leaderboard column mapping (the single most common upload-error trap) |
| 10 | Solutions (example/baseline submissions) |
| 11 | Image, terms, logo, fact-sheet |
| 12 | Zip layout (`competition.yaml` MUST be at the zip root) |
| 13 | Minimal valid bundle — copy-pasteable five-file skeleton |

Compute-worker setup, Docker image building, and server administration
are explicitly out of scope (the skill states this in its preamble).

## Provenance

Sourced verbatim from the official Codabench developer documentation at
<https://github.com/codalab/codabench/tree/develop/documentation> —
specifically these files (the skill's source map at the end lists the
exact correspondence):

| Skill section | Source file |
|---|---|
| §1 Bundle directory layout | `Organizers/Benchmark_Creation/Competition-Bundle-Structure.md` |
| §2 `competition.yaml` reference | `Organizers/Benchmark_Creation/Yaml-Structure.md` |
| §3 Versioning | `Yaml-Structure.md` |
| §4 Pages | `Yaml-Structure.md`, `Competition-Bundle-Structure.md` |
| §5 Phases | `Yaml-Structure.md` |
| §6 Tasks | `Yaml-Structure.md` |
| §7 Scoring program contract | `Competition-Bundle-Structure.md`, `Developers_and_Administrators/Submission-Docker-Container-Layout.md`, `Organizers/Benchmark_Creation/Detailed-Results-and-Visualizations.md` |
| §8 Ingestion program contract | `Competition-Bundle-Structure.md`, `Submission-Docker-Container-Layout.md` |
| §9 `scores.json` ↔ leaderboard mapping | `Organizers/Benchmark_Creation/Leaderboard-Functionality.md`, `Yaml-Structure.md` |
| §10 Solutions | `Yaml-Structure.md` |
| §11 Image, terms, logo, fact-sheet | `Yaml-Structure.md`, `Competition-Bundle-Structure.md` |
| §12 Zip layout | `Organizers/Running_a_benchmark/Update-programs-or-data.md`, `Competition-Bundle-Structure.md` |
| §13 Minimal valid bundle | **derived** — the smallest five-file surface that round-trips through `autocodabench_validate_bundle` and the Codabench platform |

When the official docs disagreed across files (e.g. `leaderboards:`
plural in `Yaml-Structure.md` vs `leaderboard:` singular in
`Competition-Bundle-Structure.md`), the skill marks the cell with
`# (docs unclear — verify on platform)` so downstream drivers don't
propagate the ambiguity silently.

The §13 minimal bundle is **engineered**, not quoted — it is the
smallest set of files that the platform accepts when validated
end-to-end. It serves as a regression smoke test for the
`autocodabench_validate_bundle` MCP tool.

## Why this file exists

The Phase 2 driver
([`autocodabench-implement`](../autocodabench-implement/README.md))
writes `competition.yaml`, scoring program metadata, pages, etc. It
needs a precise schema reference — the official docs are spread across
~10 markdown files in two top-level folders (`Organizers/`,
`Developers_and_Administrators/`) and re-fetching them on each
generation burns tokens. This skill is the consolidated, in-order
reference.

The section order mirrors the order Phase 2 writes the files (see
[autocodabench-implement/README.md](../autocodabench-implement/README.md#design-rationale))
so Phase 2 can read the skill linearly while building the bundle.

## Editing rules

If the official Codabench docs evolve:

1. Update only the affected skill section.
2. Keep the source-file pointer at the top of each cell so future edits
   trace back.
3. When the docs are silent on a field, leave the
   `# (docs unclear — verify on platform)` marker so a human knows to
   confirm before relying on it.
4. The §13 minimal bundle must continue to pass
   `autocodabench_validate_bundle` after any edit — it is the canary.

## Pointers

- Upstream source: [Codabench documentation (develop branch)][docs]
- Used by Phase 2: [`autocodabench-implement`](../autocodabench-implement/README.md)
- Bundle code path:
  [`auto_codabench/mcp_server/bundle_io.py`](../../mcp_server/bundle_io.py)
  writes the files this skill describes the shape of.
- Validator path:
  [`auto_codabench/mcp_server/tools/package.py`](../../mcp_server/tools/package.py)
  — `autocodabench_validate_bundle` enforces §9 (scores.json ↔ columns)
  and §12 (zip layout).
- Package map: [`auto_codabench/README.md`](../../README.md)

[docs]: https://github.com/codalab/codabench/tree/develop/documentation
