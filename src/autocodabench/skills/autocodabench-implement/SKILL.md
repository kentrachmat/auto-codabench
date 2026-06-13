---
name: autocodabench-implement
description: Phase 2 of an AutoCodabench session — read the locked `implementation_plan.md`, write a complete Codabench bundle, then SELF-VALIDATE end-to-end by (a) running the bundle's own baseline submission through the scoring pipeline and (b) executing the starting-kit notebook in a per-run conda env. Iterates on runtime errors (missing packages, API breaks) by installing extras or editing bundle code, capped at N attempts per artifact. STRICT EXIT: both the baseline run and the notebook MUST execute successfully for the skill to finish "pass".
---

# AutoCodabench — Phase 2: Competition Creation (self-validating)

You are running in **Phase 2**. Phase 1 saved an
`implementation_plan.md` covering all 7 design sections. Your job is
twofold:

1. **Write** a complete Codabench bundle from that plan.
2. **Self-validate** it: run the bundle's own baseline submission AND
   execute the starting-kit notebook end-to-end inside a per-run
   conda env. Iterate on runtime errors until both succeed.

This is a strict pipeline. A bundle that lints clean but whose own
baseline can't run is a broken bundle — the iteration loop catches
that class of defect before any downstream evaluator touches it.

You did NOT participate in Phase 1. The plan markdown is your single
source of truth.

---

## 0. Hard rules

1. **Read first, write second.** First three operations, in order:
   ```
   autocodabench_current_run()
   Read("<run>/specs/implementation_plan.md")   # or "<run>/implementation_plan.md"
   autocodabench_log_event(kind="stage_started",
                           payload={"stage": "8.bundle"})
   ```
   The plan is locked: don't `snapshot_spec` to overwrite it.
2. **No design decisions.** Don't pick a different metric, change the
   splits, swap the baseline. If the plan is ambiguous, pick a sensible
   default and mention it in the closing message.
3. **Validate before zipping.** `autocodabench_validate_bundle` MUST
   return clean before you call `autocodabench_zip_bundle`. Fix the
   specific issues it flags; don't paper over them.
4. **STRICT EXIT.** Both the baseline run AND the starting-kit
   notebook must finish with `ok: true` before you zip. If after
   `MAX_ATTEMPTS` you can't get both green, mark the bundle as
   `validate_runtime: false` in your closing message and DO NOT zip.
   A bundle whose own baseline can't run shouldn't ship.
5. **Adapt code, not behavior.** When iterating on a runtime failure,
   you may:
   - install missing PyPI packages via `autocodabench_install_env_extras`,
   - port an API call that broke (`tf.keras.optimizers.legacy.Adam` →
     `tf.keras.optimizers.Adam`) by editing the bundle file,
   - tighten a path / file-naming bug in `score.py` / `ingestion.py`.

   You MUST NOT:
   - change which model class the baseline is,
   - change the metric the scoring program computes,
   - change the dataset size or split,
   - shrink the bundle to fit CPU when the plan asked for GPU.
6. **Upload only on explicit user request.** Uploading creates a
   public Codabench competition.
7. **Log progress.**
   - `stage_started` for `8.bundle` at the very start,
   - one `bundle_file_written` per major artifact (yaml, scoring,
     solution, pages),
   - `attempt_started` / `attempt_finished` events for each
     baseline-run / notebook-run iteration,
   - `stage_done` at the end with the zip path (only on full success).
8. **Synthetic data is forbidden.** If the plan calls for real data
   (any dataset that exists outside `sklearn.datasets`-style synthetic
   generators) and you can't access it, STOP and report
   `validate_runtime: false` with `error: "real data inaccessible
   from this session"`. Generating fake stand-ins to make the
   pipeline complete would silently invalidate every downstream
   experiment.

---

## 1. Read the plan

```
plan_md = Read("<run>/specs/implementation_plan.md")
```

If the file is missing, the user jumped here without Phase 1:

> ⚠ I can't find `specs/implementation_plan.md`. Phase 2 needs the
> plan as input. Go back to Phase 1 — Plan to draft one, then return.

Then STOP. Don't fabricate a bundle.

When the plan IS present, send a one-paragraph confirmation:

> Reading `implementation_plan.md`. Task: <kind>; metric:
> `<sklearn func>`; baseline: `<class>`; data: <source>. I'll
> generate `competition.yaml`, `scoring_program/`, `solution/`, and
> the standard pages; lint; clone a per-run conda env; then run the
> baseline + starting-kit notebook to verify end-to-end. If anything
> errors I'll diagnose stderr and retry.

Read `src/autocodabench/skills/codabench-bundle/SKILL.md` as a reference
for `competition.yaml` shape, scoring metadata.yaml, pages, etc.

---

## 2. Generate the bundle

Generate the files in this order. Bundle slug is `meta.json → slug`
or fall back to `<branch_id>`.

### 2.1 `autocodabench_init_bundle(slug)`
Creates the bundle directory under `<run>/bundles/<slug>/`. Idempotent.

### 2.2 Scoring program — `autocodabench_write_scoring_program(slug, script, ...)`

From plan §3 (Metric) and §1 (Output shape). The script:
- Reads predictions from `input/res/`.
- Reads held-out labels from `input/ref/`.
- Computes the metric using the EXACT function the plan named.
- Writes `{"<metric_key>": <float>}` to `output/scores.json`. The key
  must match the leaderboard column key in `competition.yaml`.

**Always write a `scoring_program/requirements.txt`** alongside —
listing every non-stdlib top-level import that `score.py` itself
uses. Empty file is acceptable if pure-stdlib. Never omit; the
runtime env-prep depends on this.

### 2.3 Ingestion program — `autocodabench_write_ingestion_program(slug, script, ...)`

ONLY for code-submission (γ-style) competitions. The plan §1
"Submission protocol" field dictates this. The script:
- Reads `input_data/` (features).
- Imports the submission's `model.py` from a known path.
- Calls `Model().fit(X_train, y_train).predict(X_test)`.
- Writes predictions to `output/predictions.txt` (or `.csv`).

Also writes `ingestion_program/requirements.txt`.

### 2.4 Solution / starting kit — `autocodabench_write_solution(slug, files, subdir="solution_baseline")`

The bundle's OWN baseline. **This is what `run_baseline_submission`
will execute** to verify the pipeline. It must be runnable.

```
solutions/solution_baseline/
├── README.md                 # 1-paragraph: how to submit
├── model.py                  # the baseline class from plan §4 (γ-style)
└── predictions.txt           # OR pre-computed predictions (λ-style)
```

For γ-style, `model.py`:
```python
from sklearn.<MODULE> import <CLASS>

class Model:
    def __init__(self):
        self._clf = <CLASS>(<args from plan §4>)
    def fit(self, X, y):
        self._clf.fit(X, y); return self
    def predict(self, X):
        return self._clf.predict(X)
```

For λ-style, the baseline submission is its own predictions file —
generate them at bundle-write time by running the baseline against
the toy data the plan describes.

### 2.5 Pages — `autocodabench_write_page(slug, name, body)`

Four short markdown pages from the plan: `overview.md`,
`evaluation.md`, `terms.md`, `data.md`. Reuse plan citations.

### 2.6 Data — `autocodabench_attach_data(slug, target, ...)`

Three calls, one per target:
- `reference_data` — held-out labels for scoring.
- `input_data` — features participants see (no labels).
- `public_data` — sample data shipped in the starting kit.

If the plan names a generator (`sklearn.datasets.make_classification`,
etc.) call it, split per plan §2, and write the three targets.

### 2.7 Starting-kit notebook — write via the standard write-solution path

The starting-kit notebook (`README.ipynb` at bundle root) end-to-end
demonstrates the workflow: load the public data, instantiate the
baseline, fit / predict, write predictions, optionally invoke
scoring. **It must execute top-to-bottom without errors.** This is
the artifact `run_starting_kit` will execute.

Write it via `Write("<bundle>/README.ipynb", <ipynb_json>)` — it
isn't a registered MCP-write path but the bundle dir is writable.
Keep it short (5–10 cells). Cells must use the same imports the
baseline does, point at relative paths inside the bundle, and not
require any network access at runtime.

### 2.8 Master file — `autocodabench_write_competition_yaml(slug, payload)`

Tie everything together. See codabench-bundle SKILL.md for the full
schema. Key fields from the plan: title, version=2, phases (one
feedback + one final, dates "TBD"), tasks (scoring_program,
input_data, reference_data references), leaderboards (one column per
metric in plan §3), pages (the four written above).

Also set `docker_image:` — pick a reasonable Codabench-hosted image
matching the plan's requirements (e.g. `codalab/codalab-legacy:py3`
for pure sklearn; a tensorflow image only if the plan explicitly
asks). This choice is load-bearing: Codabench's worker runs your
programs INSIDE this image and never installs `requirements.txt`,
and the local runner does the same whenever Docker is available
(the docker engine — the per-run conda env in the next section is
the fallback for hosts without Docker, and hosts the notebook either
way). Pick an image that already ships the bundle's dependencies.

---

## 3. Lint the bundle

```
result = autocodabench_validate_bundle(slug)
```

If `result["ok"]` is False, fix every issue and re-validate. Don't
proceed with lint errors. Common ones:
- leaderboard column.key doesn't match a key in scores.json
- competition.yaml references a file path that doesn't exist
- scoring_program/metadata.yaml missing `command:`

Once clean: `autocodabench_log_event(kind="bundle_validated")`.

---

## 4. Prepare the per-run conda env

```
env = autocodabench_prepare_run_env(slug=<slug>)
```

This clones `base`, installs the union of the bundle's per-program
`requirements.txt` files via `uv pip install` (or `pip` as fallback),
and returns `env_name` + `env_python` + `package_count`. Logs land in
`<run>/run_logs/<slug>/env/`.

If `env["ok"]` is False, the per-program requirements have a defect
— the requirements union failed to install. Read the install
stderr, fix the offending `requirements.txt`, and retry. Don't move
on with a half-installed env.

Save `env_name` — every subsequent runner call needs it. (The env
serves the starting-kit notebook run in 5b and the conda fallback
engine; the baseline run in 5a executes inside the bundle's
`docker_image` instead when Docker is available. Prepare the env
regardless — the notebook always needs it.)

---

## 5. Self-validation loop (STRICT)

You will run TWO artifacts. Both must finish with `ok: true` for the
skill to pass. Each artifact has its own attempt budget; failures of
one don't consume the other's budget.

```
MAX_ATTEMPTS_BASELINE = 5
MAX_ATTEMPTS_NOTEBOOK = 4
```

### 5a. Run the bundle's own baseline

```
baseline = autocodabench_run_baseline_submission(slug, env_name)
```

The returned dict carries `ok`, `stage` (which phase failed:
"ingestion" or "scoring"), `engine` / `docker_image` / `engine_note`
(how it ran), `ingestion` / `scoring` exit codes + stderr tails,
parsed `scores`, and `sandbox_dir` for forensic inspection.

Check `engine` first: `"docker"` means the run executed inside the
bundle's declared `docker_image` exactly as Codabench will — the
platform-faithful path. `"conda"` means the per-run env was used
(no Docker daemon on this host; `engine_note` says so) — the run
verifies the programs but not the image. Diagnosis differs by
engine: under docker, a missing module means the `docker_image` YOU
declared lacks the dependency — fix `competition.yaml`'s
`docker_image` (or vendor a pure-Python module into the program
dir); `install_env_extras` affects only the conda engine.

If `baseline["ok"]` is True, log
`autocodabench_log_event(kind="baseline_passed",
payload={"scores": baseline["scores"]})` and proceed to 5b.

If False, diagnose:

| stderr pattern | fix |
|---|---|
| `ModuleNotFoundError: No module named '<X>'` | Conda engine: `autocodabench_install_env_extras(env_name, ["<pypi_name_for_X>"])`. Common map: skimage→scikit-image, cv2→opencv-python-headless, PIL→Pillow, bs4→beautifulsoup4, yaml→PyYAML, sklearn→scikit-learn. Docker engine: the declared `docker_image` lacks `<X>` — switch `competition.yaml` to an image that ships it (then re-run), or vendor a small pure-Python module into the program dir; the platform will hit the same error otherwise. |
| `ImportError: cannot import name X from Y` / `AttributeError: module Y has no attribute X` / `not supported in Keras [0-9]` | Edit the file that uses the broken API (in the BUNDLE, e.g. `solutions/solution_baseline/model.py` or `scoring_program/score.py`). Port to the available API. Common: `tf.keras.optimizers.legacy.Adam` → `tf.keras.optimizers.Adam`; `from keras.preprocessing import X` → `from tensorflow.keras.preprocessing import X`. |
| `FileNotFoundError: <path>` | Either ingestion is looking for the wrong path or the scoring stage's input/output dirs differ from Codabench convention. Re-read the relevant `metadata.yaml`'s `command:` and the script's `sys.argv` use. Fix the script. |
| `ValueError: shapes (n,m) and (p,q) ...` / metric-side numeric failure | Mismatch between baseline's predictions format and scoring's reader. Fix one or the other (whichever the plan unambiguously specifies). |
| Process killed / `SIGTERM` / no traceback | Native library conflict (e.g. abseil deadlock between TF and pyarrow). Try uninstalling the conflicting non-essential package via `install_env_extras(env_name, ["pyarrow<7"])` or pinning to a compatible version. If still failing after one such fix, fall through to "exhausted" — this class of failure is upstream of any code edit you can do. |
| **Process alive, ~0% CPU, no stderr output, hangs indefinitely** | macOS libomp / OpenBLAS / TF multi-thread deadlock. The harness sets `OMP_NUM_THREADS=1` + `TF_NUM_INTEROP_THREADS=1` + `TF_NUM_INTRAOP_THREADS=1` + sibling vars as defaults in the subprocess env BEFORE python starts, so this should be already handled. If it still hangs, the bundle's own code is overriding one of those vars somewhere — grep `scoring_program/`, `ingestion_program/`, `solutions/` for `OMP_NUM_THREADS` / `TF_NUM_*` and remove the override. Do NOT add `os.environ.setdefault("OMP_NUM_THREADS", "1")` to bundle code as a "fix" — by the time that line runs, libomp is already loaded with the wrong thread count. |

The harness automatically exports the following defaults into every
subprocess it launches (set BEFORE the child python starts, so libomp
and BLAS read them at .so-load time): `OMP_NUM_THREADS=1`,
`OPENBLAS_NUM_THREADS=1`, `MKL_NUM_THREADS=1`,
`VECLIB_MAXIMUM_THREADS=1`, `NUMEXPR_NUM_THREADS=1`,
`TF_NUM_INTEROP_THREADS=1`, `TF_NUM_INTRAOP_THREADS=1`,
`TF_CPP_MIN_LOG_LEVEL=2`, `PYTHONUNBUFFERED=1`. You can override
any of them per-call via the `extra_env` arg on
`autocodabench_run_baseline_submission`, `_run_user_submission`, or
`_run_starting_kit`, but the defaults are intentional — they
prevent the macOS deadlock. The toy-scale data used in self-validation
doesn't benefit from multi-threading anyway.

Re-run baseline. Cap at `MAX_ATTEMPTS_BASELINE`. If exhausted, set
`validate_runtime = false`, record `baseline_status = "fail"` in the
closing message, and SKIP to step 7 (do not zip).

### 5b. Run the starting-kit notebook

```
nb = autocodabench_run_starting_kit(slug, env_name)
```

Returns `ok`, `cells_executed`, `exit_code`, `stderr_tail`,
`executed_notebook` (path to the executed copy for review).

If `nb["ok"]` is True, log
`autocodabench_log_event(kind="starting_kit_passed",
payload={"cells_executed": nb["cells_executed"]})` and proceed to 6.

If False, diagnose using the same rule table as 5a — most failures
are about imports, paths, or API breaks. Edit `README.ipynb` cells
directly via `Write` / `Edit`. **Do not change what the notebook
demonstrates** — narrate the same baseline against the same data; only
fix mechanical errors.

Cap at `MAX_ATTEMPTS_NOTEBOOK`. If exhausted, set
`validate_runtime = false`, record `notebook_status = "fail"`, and
SKIP to step 7.

---

## 6. Zip

Only reached if both 5a and 5b passed.

```
result = autocodabench_zip_bundle(slug)
```

Returns `{"zip_path": "<.../bundles/<slug>/<slug>.zip>"}`.

---

## 7. Closing message + log

Emit one of two closing blocks.

### 7a. Full pass

```
✅ **Bundle ready** (validated + self-tested).

  bundle zip       — `<run>/bundles/<slug>/<slug>.zip` (≈<N> MB)
  validate_bundle  — passed
  baseline_run     — passed: <metric_key>=<score> in <N>s
  starting_kit     — passed: <N> cells executed in <M>s
  env              — `<env_name>` (<package_count> packages, install via <uv|pip>)

Choices I made where the plan was ambiguous:
  - <list any defaults you picked, or "none — the plan was fully concrete">
```

Then:
```
autocodabench_log_event(
    kind="stage_done",
    payload={"stage": "8.bundle",
             "zip_path": "...",
             "validate_bundle": true,
             "validate_runtime": true,
             "baseline_scores": {...},
             "cells_executed": <int>,
             "env_name": "<...>"},
)
```

### 7b. Validation/runtime failure

```
⚠ **Bundle written but NOT runtime-validated**.

  validate_bundle  — passed
  baseline_run     — <pass|fail>: <one-line reason if fail>
  starting_kit     — <pass|fail>: <one-line reason if fail>
  env              — `<env_name>`
  attempts used    — baseline: <K>/5, notebook: <K>/4

Last failing stderr (excerpted):
  <last ~10 lines>

Forensic artifacts:
  - `<run>/run_logs/<slug>/baseline/`
  - `<run>/run_logs/<slug>/starting_kit/`
  - `<run>/run_logs/<slug>/env/`

The bundle is NOT zipped. Read the run logs to decide whether this
is a bundle defect, an env defect, or a host-side compatibility
issue. The bundle dir itself is intact at `<run>/bundles/<slug>/`
for inspection / hand-editing.
```

Then:
```
autocodabench_log_event(
    kind="stage_failed",
    payload={"stage": "8.bundle",
             "validate_bundle": true,
             "validate_runtime": false,
             "baseline_status": "<pass|fail>",
             "notebook_status": "<pass|fail>",
             "baseline_error": "<...>",
             "notebook_error": "<...>",
             "attempts_used": {"baseline": <K>, "notebook": <K>}},
)
```

---

## 8. Things to avoid

- ❌ Zipping a bundle whose baseline can't run. The runtime check IS
  the validation that matters; the lint is just necessary, not
  sufficient.
- ❌ "Fixing" a failure by changing what the baseline does. If the
  plan says EfficientNetV2 and TF complains, you may port the import
  / optimizer call, but you may NOT swap to logistic regression
  because "it's simpler".
- ❌ Generating synthetic data when the plan asked for a real
  dataset. Stop and report instead.
- ❌ Calling `install_env_extras` to install something the plan
  asked for but you forgot to put in `requirements.txt`. The right
  fix is to add it to the bundle's `requirements.txt` AND
  `install_env_extras` so this session can proceed — that way the
  bundle is correct for any future session too.
- ❌ Catching exceptions inside `score.py` to make the run "succeed".
  If scoring can't read the predictions, that's a bug to fix, not to
  swallow.
- ❌ Touching `ground_truth/` (you don't have read access to it, but
  if you somehow do — don't).
