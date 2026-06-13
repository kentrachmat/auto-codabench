# `autocodabench-implement` — Phase 2 driver

**Skill kind:** driver (subagent orchestrator).
**Skill name:** `autocodabench-implement`.
**File:** [`SKILL.md`](./SKILL.md).

## What it does

Drives Phase 2 of an AutoCodabench session:

1. Reads `<run>/specs/implementation_plan.md` (locked — produced by
   Phase 1; not editable here).
2. Writes the Codabench bundle via the MCP `autocodabench_write_*`
   tools — `competition.yaml`, scoring program, solution / starting
   kit, four pages, data attachments.
3. **Lints** via `autocodabench_validate_bundle`. Fixes issues until
   clean.
4. **Prepares a per-run conda env** via
   `autocodabench_prepare_run_env` (clone base + install per-program
   `requirements.txt` via `uv` or pip).
5. **Self-validates at runtime**: runs the bundle's OWN baseline via
   `autocodabench_run_baseline_submission`, then executes the
   starting-kit notebook via `autocodabench_run_starting_kit`.
   Iterates on runtime errors (install missing packages via
   `autocodabench_install_env_extras`, edit broken API call sites in
   the bundle), capped at 5 / 4 attempts respectively. STRICT: both
   must succeed or the skill exits with `validate_runtime: false` and
   the bundle is NOT zipped.
6. Zips via `autocodabench_zip_bundle` (only on full pass).
7. Optional: uploads to Codabench when the user types
   "publish" / "upload" in chat. The web UI's **Publish form** is the
   canonical upload path and bypasses the LLM entirely — this skill
   defers to the form unless the user explicitly asks in chat.

The runtime self-validation step exists because a bundle that lints
clean but whose own baseline can't run is a broken bundle. The class
of failure caught here (missing PyPI deps, Keras-2-vs-Keras-3 API
breaks, λ-vs-γ submission protocol mix-ups) would otherwise only
surface on Codabench's compute workers, hours after the user
believed the bundle was done.

## Why it's a *driver*, not a *knowledge* skill

Like [`autocodabench-plan`](../plan/README.md), this skill orchestrates
calls into the reference skills and the MCP tool layer.

- [`codabench-bundle`](../codabench-bundle/README.md) — the **schema**
  reference. Phase 2 consults this for `competition.yaml` keys,
  scoring-program `metadata.yaml` shape, leaderboard column ↔
  `scores.json` key contract, zip layout rules.
- [`competition-design`](../competition-design/README.md) — occasionally,
  for picking a sensible default when the plan is ambiguous (e.g. metric
  defaults from §3).

This skill doesn't carry the schema content itself — it tells Phase 2
*the order* in which to write files and *the constraints* between them
(validate before zip; never overwrite the locked plan; upload only on
explicit user request).

## Design rationale

### The §2 file-generation order is deliberately bottom-up

`init` → `scoring_program` → `solution` → `pages` → `data` →
`competition.yaml`.

- The scoring program defines the `scores.json` keys.
- The solution defines the submission-interface contract (this is also
  what the bundle-creation-test experiment's
  `autocodabench-reformat-and-run` skill reads to bridge a ground-truth
  `submission.py` to whatever the bundle declared — see
  [`autocodabench/skills/autocodabench-reformat-and-run/SKILL.md`](../autocodabench-reformat-and-run/SKILL.md)).
- The pages reference the metric and data sources declared above.
- `competition.yaml` is written **last** because its `tasks:` and
  `leaderboards:` blocks reference all the above. Writing it first
  would mean stubbing and back-filling.

### "Validate before zip"

`autocodabench_zip_bundle` will happily produce a zip that the
Codabench platform later rejects; the local validator catches most of
these (e.g. leaderboard column key not matching a `scores.json` key,
missing referenced file paths,
[`competition.yaml` not at zip root](../codabench-bundle/SKILL.md)). This
is the cheapest place to catch them — earlier than upload, with no
network round-trip.

### "Upload only on explicit user request"

Uploading creates a **public** Codabench competition. Implicit uploads
from the LLM are a surprise the user can't reverse. The web Publish
form is the default path because it takes the username/password
deterministically and never burns LLM cost on a 4-step REST flow.

## How this skill was generated

- **The §2 file-generation order** is the canonical order observed
  across the first end-to-end test runs during dev. It is also what
  the [`codabench-bundle`](../codabench-bundle/README.md) skill is
  ordered to support — read §§7–9 (scoring → leaderboard) before §§5–6
  (phases → tasks) and the bottom-up flow falls out naturally.
- **The §3 (validate) and §4 (zip) stages** were added once the MCP
  tool layer stabilised. Earlier runs zipped before validating and
  produced bundles the Codabench platform rejected on upload.
- **The §1 user-facing summary template**
  ("Reading `implementation_plan.md`. Task: <kind>; metric:
  `<sklearn func>`; baseline: `<class>`; data: <source + ~rows>")
  was added because early test runs would silently misread the plan and
  produce a bundle for a different task, with no way for the user to
  notice before zipping. The summary forces an early read-back the user
  can correct.
- **The "plan missing" handler** (§7) was added to prevent Phase 2 from
  fabricating a competition from scratch when a user jumped directly to
  the bundle phase without doing Phase 1.

## Pointers

- Reads only: `<run>/specs/implementation_plan.md`
- Knowledge it cites: [`codabench-bundle`](../codabench-bundle/README.md), [`competition-design`](../competition-design/README.md)
- MCP tools used:
  - file-writers: `init_bundle`, `write_competition_yaml`,
    `write_scoring_program`, `write_ingestion_program`,
    `write_solution`, `write_page`, `attach_data`,
    `validate_bundle`, `zip_bundle`, `upload_bundle`
  - runtime self-validation: `prepare_run_env`,
    `install_env_extras`, `run_baseline_submission`,
    `run_starting_kit`, `remove_run_env`
  - run-state: `current_run`, `log_event`
- Phase orchestrator in code:
  [`web/app.py`](../../../web/app.py) →
  `_advance_to_phase(PHASE_BUNDLE)`
- Phase 2 produces:
  `<run>/bundles/<slug>/{competition.yaml, scoring_program/, solution/, pages/, README.ipynb, <slug>.zip}`
  + `<run>/run_logs/<slug>/{env,baseline,starting_kit}/`
- Package map: [`docs/architecture.md`](../../../../docs/architecture.md)
