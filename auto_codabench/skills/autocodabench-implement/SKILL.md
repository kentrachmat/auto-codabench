---
name: autocodabench-implement
description: Phase 2 of an AutoCodabench session (web v1) — read the locked `implementation_plan.md` from Phase 1, then write a complete Codabench bundle (competition.yaml + scoring_program/ + solution/ + pages/) directly from the plan's specifications, validate it, zip it, and surface a download link. If the user asks, also upload the bundle to Codabench and return the competition URL. No intermediate notebook step.
---

# AutoCodabench — Phase 2: Competition Creation

You are running in **Phase 2** of an AutoCodabench session. Phase 1
saved an `implementation_plan.md` covering all 7 design sections of a
Codabench competition. Your job is to turn that plan into a working
Codabench bundle and (if the user asks) publish it.

You did NOT participate in Phase 1. The plan markdown is your single
source of truth. If a field is genuinely missing or ambiguous, pick a
sensible default (sklearn-class baseline, `f1_score(average='macro')`,
etc.) and note the choice in the closing message — don't block.

When the zip is ready and validated, tell the user where the download
link is and offer to upload. Phase 2 is the **terminal** phase; there's
no Phase 3.

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
2. **No design decisions.** Don't pick a different metric, change
   the splits, swap the baseline. If the plan is ambiguous, pick a
   sensible default and mention it in the closing message.
3. **Validate before zipping.** `autocodabench_validate_bundle` MUST
   return clean before you call `autocodabench_zip_bundle`. Fix the
   specific issues it flags; don't paper over them.
4. **Upload only on explicit user request.** Uploading creates a
   public Codabench competition. The user must say "publish" /
   "upload" / "push to Codabench" / "go". A silent successful zip
   is the default success state.
5. **Log progress.**
   - `stage_started` for `8.bundle` at the very start,
   - one `bundle_file_written` per major artifact (yaml, scoring,
     solution, pages),
   - `stage_done` at the end with the zip path + (if uploaded) the
     competition URL.

---

## 1. Read the plan

Use the `Read` tool — all on local disk:

```
plan_md = Read("<run>/specs/implementation_plan.md")
```

If the file is missing, the user jumped here without doing Phase 1.
Say so:

> ⚠ I can't find `specs/implementation_plan.md`. Phase 2 builds the
> competition bundle FROM the plan; without a plan I'd be inventing
> the design from scratch and the cost-savings of phase isolation
> evaporate. Click **« Back to Phase 1 — Plan** in the phase bar at
> the top to draft one, then return here.

Then STOP — don't write the bundle without a plan.

When the plan IS present, send a one-paragraph user-facing summary so
both sides know you have the right artifacts loaded:

> Reading `implementation_plan.md`. Task: <task kind>; metric:
> `<sklearn func>`; baseline: `<class>`; data: <source + ~rows>. I'll
> generate `competition.yaml`, `scoring_program/`, `solution/`, and
> the four standard pages, validate, and zip.

Also read `auto_codabench/skills/codabench-bundle/SKILL.md` for the
authoritative bundle schema reference (competition.yaml shape, scoring
program metadata.yaml, pages, phases). You don't need to memorise it
— just consult it when you need a specific YAML key.

---

## 2. Generate the bundle

Generate the files in this order. The bundle slug is the run's
`meta.json → slug` (fall back to `<branch_id>` if slug is empty).

### 2.1 `autocodabench_init_bundle(slug)`
Creates the bundle directory tree under
`auto_codabench/bundles/<slug>/`. Idempotent.

### 2.2 Scoring program — `autocodabench_write_scoring_program(slug, score_py, metadata_yaml)`

Generate `score.py` from the plan's §3 (Metric) and §1 (Output shape).
The function should:
- Read `prediction.txt` (or `predictions.csv`) from the input dir
  Codabench populates.
- Read held-out labels from `reference_data/`.
- Compute the metric with the EXACT function the plan named (e.g.
  `sklearn.metrics.f1_score(y_true, y_pred, average='macro')`).
- Write `{"score": <float>}` to `scores.json` (key matches the
  leaderboard column).

`metadata_yaml` is the scoring program's `metadata.yaml` — see
codabench-bundle SKILL.md §3 for the exact shape (one line:
`command: python score.py`).

Reference template for score.py:

```python
import json, os, sys, numpy as np, pandas as pd
from sklearn.metrics import <FUNC>  # use the exact name from plan §3

def main(input_dir, output_dir, reference_dir):
    y_pred = np.loadtxt(os.path.join(input_dir, "predictions.txt"))
    y_true = np.loadtxt(os.path.join(reference_dir, "labels.txt"))
    score  = float(<FUNC>(y_true, y_pred, <args from plan>))
    with open(os.path.join(output_dir, "scores.json"), "w") as f:
        json.dump({"score": score}, f)

if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2], sys.argv[3])
```

### 2.3 Solution / starting kit — `autocodabench_write_solution(slug, files)`

The minimum viable starting kit for a result-submission (λ) competition.
`files` is a dict mapping relative path → file content:

```
solution/
├── README.md                       # 1-paragraph: how to submit
├── sample_code_submission/
│   └── model.py                    # the baseline class from plan §4
└── sample_data/
    └── ...                         # tiny example data
```

`model.py` template (substitute the plan's baseline class):

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

Include a 10-line snippet in `README.md` showing how to train + write
`predictions.txt`.

### 2.4 Pages — `autocodabench_write_page(slug, name, body)`

Four short markdown pages, derived from the plan:

- `overview.md` — competition motivation. Pull from plan §1 (5W) and
  any plan citations.
- `evaluation.md` — the metric + scoring procedure. From plan §3.
- `terms.md` — license, citation requirement, IRB posture. From
  plan §5, §6, §7 (license).
- `data.md` — data sources + splits + access. From plan §2.

Reuse citations from the plan; don't re-search.

### 2.5 Data — `autocodabench_attach_data(slug, kind, files)`

Three calls, one per `kind`:
- `reference_data` — held-out labels Codabench uses for scoring.
- `input_data`     — features participants see (no labels).
- `public_data`    — sample data shipped in the starting kit.

For v1 (toy data per plan §2), generate these from the plan's named
data source. E.g. if plan says `sklearn.datasets.make_classification`,
call it with the sizes from the plan, split, save train as
`public_data` and test (features → `input_data`, labels →
`reference_data`).

### 2.6 Master file — `autocodabench_write_competition_yaml(slug, body)`

Tie everything together. See codabench-bundle SKILL.md §1 for the full
schema. Key fields from the plan:
- `title`: slug-derived or the user's competition idea verbatim.
- `version: 2`.
- `phases`: one feedback + one final, dates left as placeholders
  (e.g. `start: "TBD"`) — the user fills those in Codabench's UI
  before publishing.
- `tasks`: one per phase, pointing at scoring program / data /
  submission template.
- `leaderboards`: one column per metric in plan §3.
- `pages`: list the four pages from §2.4.

---

## 3. Validate

```
result = autocodabench_validate_bundle(slug)
```

If `result["ok"]` is False, fix the specific issues (missing
referenced files, leaderboard column not matching `scores.json` keys,
wrong YAML key, …) and re-validate. Don't move on to zipping with
validation errors.

---

## 4. Zip

```
result = autocodabench_zip_bundle(slug)
```

Returns `{"zip_path": "<.../bundles/<slug>/<slug>.zip>"}`.

The web layer copies this zip to the public dir so the user can
download it from the workspace panel.

---

## 5. Closing message + log

Render this closing block:

```
✅ **Bundle ready**.

  bundle zip       — `<run>/bundles/<slug>/<slug>.zip` (≈<N> MB)
  validate_bundle  — passed
  files written    — <n>
  codabench URL    — <url if uploaded; otherwise "(not yet uploaded)">

A 📦 bundle.zip tab is now in the workspace panel on the right.
Click it to download. To publish to Codabench directly, click
**⬆️ Upload to Codabench** below (the button will appear in chat) —
that needs `CODABENCH_USERNAME` + `CODABENCH_PASSWORD` configured on
the Space.

Choices I made where the plan was ambiguous:
  - <list any defaults you picked here, or "none — the plan was fully concrete">
```

Then:

```
autocodabench_log_event(
    kind="stage_done",
    payload={"stage": "8.bundle",
             "zip_path": "...",
             "validated": true,
             "uploaded": <bool>,
             "competition_url": "<url or null>"},
)
```

Stage 8 marked DONE → the web layer surfaces the Download + Upload
affordances. STOP.

---

## 6. Optional: publish

Only if the user explicitly asked ("publish" / "upload" / "push to
Codabench" / "go"):

```
result = autocodabench_upload_bundle(slug)
```

Requires `CODABENCH_USERNAME` + `CODABENCH_PASSWORD` (or
`CODABENCH_TOKEN`) in env. Returns
`{"competition_id": <int>, "competition_url": "https://www.codabench.org/competitions/<id>/"}`.

Surface the URL prominently as a clickable markdown link in your
closing message.

---

## 7. If things go sideways

- **Plan file missing** → see §1; don't fabricate. Point at the
  Back-to-Phase-1 pill.
- **`current_run()` returns `opened: False`** → ASK which run dir.
  Don't open a new one (that would orphan the user's plan).
- **`validate_bundle` fails on a metric mismatch** → the
  `scores.json` key doesn't match the leaderboard column. Re-read
  plan §3 to confirm the metric name and align both.
- **zip > 100 MB** → you're shipping too much sample data. Reduce to
  ~10 rows; the held-out data goes in `reference_data` /
  `input_data`, not in the participant-facing zip.
- **Upload returns 401/403** → bad credentials. Surface the error
  verbatim and suggest the user verify `CODABENCH_USERNAME` /
  `CODABENCH_PASSWORD` in the Space's Repository Secrets.
