---
name: codabench-bundle
description: Technical schema and conventions for a Codabench competition bundle (competition.yaml, pages, phases, scoring program, ingestion program, scores.json↔leaderboard mapping, zip layout). Use when generating any file inside a bundle.
---

# Codabench Bundle: Technical Reference

This skill is the schema-level reference for *generating* a Codabench
competition bundle on disk. It only covers what is needed to produce a
lint-clean, upload-ready `.zip`. Compute-worker setup, Docker image
building, and server administration are explicitly out of scope.

A Codabench competition bundle is **a zip file** containing a top-level
`competition.yaml` plus the assets it references. The platform unpacks
the zip server-side; everything the bundle declares must resolve to a
file path relative to `competition.yaml`.

Set `version: 2` at the top of every new bundle. v1 and v1.5 are legacy.

---

## 1. Bundle directory layout

The structure below is the canonical layout. Filenames are flexible —
what matters is that they match the paths referenced from
`competition.yaml`.

```
my_competition/                       # the directory you will zip the CONTENTS of
├── competition.yaml                  # required, at the root of the zip
├── logo.png                          # competition logo, referenced by `image:`
├── pages/                            # markdown/HTML pages shown as tabs
│   ├── overview.md                   # conventional landing tab
│   ├── evaluation.md                 # how submissions are scored
│   ├── terms.md                      # T&Cs (also referenced by `terms:`)
│   ├── data.md                       # data description
│   └── starting_kit.md               # (optional) link to / describe the starter
├── scoring_program/                  # required by every task
│   ├── metadata.yaml                 # has a `command:` key, see §7
│   ├── scoring.py                    # or any executable referenced by command
│   └── ...                           # helper modules, requirements, etc.
├── ingestion_program/                # required only for code-submission tasks
│   ├── metadata.yaml                 # has a `command:` key, see §8
│   └── ingestion.py
├── input_data/                       # data used by ingestion program/prediction step
│   └── ...                           # mounted at /app/input_data on the worker
├── reference_data/                   # ground truth, only the scoring program sees it
│   └── truth.csv                     # mounted at /app/input/ref
├── solutions/                        # example/baseline submissions
│   ├── solution1.zip
│   └── solution2/
├── starting_kit/                     # (optional) downloadable starter for participants
│   └── ...
└── public_data/                      # (optional) downloadable public data
    └── ...
```

Notes:
- `scoring_program/`, `ingestion_program/`, `input_data/`,
  `reference_data/`, `solutions/*` can each be supplied as a directory
  **or** as a pre-zipped archive (`scoring_program.zip` etc.) sitting
  at the bundle root. The YAML simply points at whichever form you used.
- File paths in `competition.yaml` are **always relative to
  `competition.yaml`**.
- Reference scaffolds for the two programs ship next to this skill in
  [`templates/scoring.py`](./templates/scoring.py) and
  [`templates/ingestion.py`](./templates/ingestion.py). They use the exact
  Codabench worker paths (`/app/input/ref`, `/app/input/res`, `/app/output`,
  `/app/input_data`, `/app/program`, `/app/ingested_program`) under a
  `--codabench` switch; start from them and fill in the `NotImplementedError`
  bodies rather than writing the boilerplate by hand.

---

## 2. `competition.yaml` reference

Top-level keys, with type, required/optional, and an example. Drawn
verbatim from `Yaml-Structure.md` and `Competition-Bundle-Structure.md`.

### Required top-level keys

| key      | type   | notes                                                                                      |
|----------|--------|--------------------------------------------------------------------------------------------|
| `version`| int    | Must be `2` for any new bundle.                                                            |
| `title`  | string | Competition title.                                                                         |
| `image`  | path   | Path to competition logo (png/jpg), relative to `competition.yaml`.                        |
| `terms`  | path   | Path to a markdown or HTML page with terms of participation.                               |
| `pages`  | list   | List of `{title, file}` entries shown as tabs. See §4.                                     |
| `phases` | list   | At least one phase. See §5.                                                                |
| `tasks`  | list   | At least one task. See §6.                                                                 |
| `leaderboards` | list | At least one leaderboard with at least one column. See §9.                            |

Note: the docs use both `leaderboards:` (plural, in `Yaml-Structure.md`)
and `leaderboard:` (singular, in `Competition-Bundle-Structure.md` and
`Leaderboard-Functionality.md`). Both appear in official examples.
Prefer `leaderboards:` per the structure doc.
`# (docs unclear — verify on platform)`

### Optional top-level keys

| key                                          | type    | default                       | meaning                                                              |
|----------------------------------------------|---------|-------------------------------|----------------------------------------------------------------------|
| `description`                                | string  | —                             | Short blurb shown on listing.                                        |
| `registration_auto_approve`                  | bool    | `False`                       | If `True`, participation requests skip manual approval.              |
| `docker_image`                               | string  | `codalab/codalab-legacy:py3`  | DockerHub `username/image:tag`.                                      |
| `make_programs_available`                    | bool    | —                             | Share ingestion+scoring program with participants.                   |
| `make_input_data_available`                  | bool    | —                             | Share input data with participants.                                  |
| `queue`                                      | string  | default queue                 | Vhost of a custom compute queue.                                     |
| `enable_detailed_results`                    | bool    | `False`                       | Watch for `detailed_results.html` from scoring program (see §7).     |
| `show_detailed_results_in_submission_panel`  | bool    | `True`                        |                                                                      |
| `show_detailed_results_in_leaderboard`       | bool    | `True`                        |                                                                      |
| `contact_email`                              | string  | —                             | Organizer contact.                                                   |
| `reward`                                     | string  | —                             | Free-form reward string e.g. `"$1000"`.                              |
| `auto_run_submissions`                       | bool    | `True`                        | If `False`, organizer must trigger each run.                         |
| `can_participants_make_submissions_public`   | bool    | `True`                        |                                                                      |
| `forum_enabled`                              | bool    | `True`                        |                                                                      |
| `solutions`                                  | list    | —                             | Example solutions; see §10.                                          |
| `fact_sheet`                                 | mapping | —                             | Per-submission metadata questionnaire (see `Yaml-Structure.md`).     |

### Full top-level example

Quoted verbatim from `Yaml-Structure.md`:

```yaml
# Required
version: 2
title: Compute Pi
image: images/pi.png
terms: pages/terms.md

# Optional
description: Calculate pi to as many digits as possible, as quick as you can.
registration_auto_approve: True
docker_image: codalab/codalab-legacy:py37 # default docker image
make_programs_available: True
make_input_data_available: False
enable_detailed_results: True
show_detailed_results_in_submission_panel: True
show_detailed_results_in_leaderboard: True
contact_email: organizer_email@example.com
reward: $1000 prize pool
auto_run_submissions: True
can_participants_make_submissions_public: False
forum_enabled: True
```

---

## 3. Versioning

```yaml
version: 2
```

Must be set at the top. Without it the platform may try to parse the
bundle as a v1.5 (legacy CodaLab) competition. v1.5 is supported for
backward compatibility but not all features are available; generate v2.

---

## 4. Pages

`pages:` is a list of `{title, file}` mappings. `file` is a path,
relative to `competition.yaml`, of a markdown (`.md`) or HTML (`.html`)
file. The order in the YAML is the order shown as tabs in the UI.

```yaml
pages:
  - title: Welcome
    file: welcome.md
  - title: Getting started
    file: pages/getting_started.html
```

Both fields are required per page.

### Conventional pages

The example bundle in `Competition-Bundle-Structure.md` uses these
tabs:

```yaml
pages:
    - title: overview
      file: overview.md
    - title: evaluation
      file: evaluation.md
    - title: terms
      file: terms_and_conditions.md
    - title: data
      file: data.md
```

Recommended page set for a fresh bundle:

| title          | purpose                                                          |
|----------------|------------------------------------------------------------------|
| `overview`     | What the competition is, motivation, dates, prizes.              |
| `evaluation`   | Metric definitions, scoring formula, what `scores.json` returns. |
| `data`         | Description of input + reference data, license, format.          |
| `terms`        | T&Cs (often the same file as the top-level `terms:` key).        |
| `starting_kit` | (optional) Where to download the starter, baseline scores.       |

The `terms:` top-level key is independent of the `pages:` list — it can
point at the same markdown file shown in a tab, or a separate file
that's only shown at the join-competition gate.

---

## 5. Phases

Each entry in `phases:` defines an active window during which a fixed
set of tasks accept submissions. Phases must be sequential and
non-overlapping.

### Required per phase

| key      | type   | notes                                                                       |
|----------|--------|-----------------------------------------------------------------------------|
| `name`   | string | Phase name shown in the UI.                                                 |
| `start`  | string | ISO datetime, `YYYY-MM-DD HH:MM:SS`, UTC.                                   |
| `end`    | string | ISO datetime. Optional **only** for the final phase (then phase is open-ended). |
| `tasks`  | list[int] | Indexes into the top-level `tasks:` list.                                |

### Optional per phase

| key                              | type   | meaning                                                                                                  |
|----------------------------------|--------|----------------------------------------------------------------------------------------------------------|
| `index`                          | int    | Order. If omitted, declaration order wins.                                                               |
| `description`                    | string | Shown on the phase tab.                                                                                  |
| `max_submissions`                | int    | Hard cap **per participant for the entire phase**.                                                       |
| `max_submissions_per_day`        | int    | Cap per participant per UTC day (midnight-to-midnight).                                                  |
| `auto_migrate_to_this_phase`     | bool   | Cannot be set on the first phase. Re-submits all successful submissions from the previous phase.         |
| `execution_time_limit`           | int    | Per-submission wall time in **seconds**. Default `600`.                                                  |
| `hide_output`                    | bool   | If `True`, stdout/stderr hidden from non-admins.                                                         |
| `hide_prediction_output`         | bool   | Hide "Output from prediction step" download.                                                             |
| `hide_score_output`              | bool   | Hide "Output from scoring step" download (i.e. `scores.txt`).                                            |
| `starting_kit`                   | path   | Folder participants can download. Put example submissions, notebooks, docs.                              |
| `public_data`                    | path   | Folder of public data participants can download.                                                         |
| `accepts_only_result_submissions`| bool   | Default `False`. When `True`, the phase accepts only result (predictions) submissions, not code.         |

Note on per-day vs per-person caps: the YAML doc only defines
`max_submissions` (per-phase, per-participant) and
`max_submissions_per_day`. The "max submissions per person" wording
appears in the editor UI but is just the editor's name for
`max_submissions`. `# (docs unclear — verify on platform)`

### Public vs private phases

There is no explicit `public:` / `private:` toggle on a phase in
`Yaml-Structure.md`. Visibility is competition-wide (controlled by the
Publish checkbox in the editor's Details tab), and `hide_*` flags hide
*outputs* rather than the phase itself.
`# (docs unclear — verify on platform)`

### Example

```yaml
phases:
  - index: 0
    name: Development Phase
    description: Tune your models
    start: 2019-12-12 13:30:00  # Time in UTC+0 and 24-hour format
    end: 2020-02-01 00:00:00  # Time in UTC+0 and 24-hour format
    execution_time_limit: 1200
    starting_kit: starting_kit
    public_data: public_data
    accepts_only_result_submissions: True
    tasks:
      - 0
  - index: 1
    name: Final Phase
    description: Final testing of your models
    start: 2020-02-02 00:00:00 # Time in UTC+0 and 24-hour format
    auto_migrate_to_this_phase: True
    accepts_only_result_submissions: False
    tasks:
      - 1
```

---

## 6. Tasks

A task is the unit a phase runs. `phases[].tasks` is a list of integer
indexes into this top-level `tasks:` list.

### Required per task

| key              | type   | notes                                                                                |
|------------------|--------|--------------------------------------------------------------------------------------|
| `index`          | int    | Used by `phases[].tasks` and `solutions[].tasks`.                                    |
| `name`           | string | Task name.                                                                            |
| `scoring_program`| path   | Path to a directory **or** `.zip` containing the scoring program.                    |

Alternative (referencing a server-side task):

| key   | type   | notes                                                                                              |
|-------|--------|----------------------------------------------------------------------------------------------------|
| `key` | UUID   | UUID of an existing task in the database. **All other fields except `index` are ignored** when this is set. |

For locally-generated bundles you almost always use `scoring_program`
plus the data/program paths below, not `key`.

### Optional per task

| key                          | type   | notes                                                                                                            |
|------------------------------|--------|------------------------------------------------------------------------------------------------------------------|
| `description`                | string | Short blurb.                                                                                                     |
| `input_data`                 | path   | Data for the prediction step. Goes to `/app/input_data` on the worker.                                           |
| `reference_data`             | path   | Ground truth seen only by the scorer. Goes to `/app/input/ref`.                                                  |
| `ingestion_program`          | path   | Ingestion program directory or zip. See §8.                                                                      |
| `ingestion_only_during_scoring` | bool | If `True`, the ingestion program runs in parallel with the scoring program and they share `/app/shared`.         |

### What goes in each directory

- **`input_data/`** — In a *result-submission* contest this is unused
  on the worker (participants upload predictions directly). In a
  *code-submission* contest this is the public input the participant's
  code must read in order to produce predictions.
- **`reference_data/`** — Ground truth used by the scorer to grade
  predictions. Never exposed to the submission container.
- **`scoring_program/`** — A directory (or `.zip`) containing at least
  a `metadata.yaml` and the script(s) it runs. See §7.
- **`ingestion_program/`** — A directory (or `.zip`) containing at
  least a `metadata.yaml` and the script(s) it runs. Required only for
  code-submission contests. See §8.

### Example

```yaml
tasks:
  - index: 0
    name: Compute Pi Developement Task
    description: Compute Pi, focusing on accuracy
    input_data: dev_phase/input_data/
    reference_data: dev_phase/reference_data/
    ingestion_program: ingestion_program.zip
    scoring_program: scoring_program.zip
  - index: 1
    name: Compute Pi Final Task
    description: Compute Pi, speed and accuracy matter
    input_data: final_phase/input_data/
    reference_data: final_phase/reference_data/
    ingestion_program: ingestion_program.zip
    scoring_program: scoring_program.zip
```

---

## 7. Scoring program contract

Every task needs a scoring program. The scoring program is a directory
(or `.zip`) containing:

1. **`metadata.yaml`** with at least a `command:` key that tells the
   worker how to launch the program.
2. The script(s) and any support files referenced by that command.

### `metadata.yaml`

Quoted verbatim from `Competition-Bundle-Structure.md`:

```yaml title="metadata.yaml"
command: python3 /app/program/scoring.py /app/input/ /app/output/
```

The `command:` is executed inside the submission container. By
convention the scoring script is placed at `/app/program/<name>.py`
because the platform extracts the scoring program archive into
`/app/program`.

### Filesystem the scoring step sees

Quoted from `Submission-Docker-Container-Layout.md`:

| path                    | purpose                                                                                                |
|-------------------------|--------------------------------------------------------------------------------------------------------|
| `/app/program/`         | Where the scoring program (and/or ingestion program) is extracted. Always exists.                      |
| `/app/output/`          | Where the scoring program **must** write its outputs. Always exists.                                   |
| `/app/input/`           | Root of inputs for the scoring step. Exists only on the scoring step.                                  |
| `/app/input/ref/`       | Reference (truth) data. Only available on scoring step. Not visible to submissions.                    |
| `/app/input/res/`       | Predictions / output from the prediction step (or directly from a result submission).                  |
| `/app/shared/`          | Shared dir between ingestion program and submission (only when ingestion runs during scoring).         |
|

Scoring program common-case Python boilerplate (paths confirmed by
`Detailed-Results-and-Visualizations.md`):

```python
input_dir = '/app/input'                       # root of scoring inputs
output_dir = '/app/output/'                    # where scores.json is written
reference_dir = os.path.join(input_dir, 'ref') # ground truth
prediction_dir = os.path.join(input_dir, 'res')# predictions
score_file = os.path.join(output_dir, 'scores.json')
html_file  = os.path.join(output_dir, 'detailed_results.html')  # optional
```

*NOTE:* use two sets of paths when creating scoring program, set 1 to be used by Codabench, set 2 to be used locally for testing the scripts. To check a scoring template example, see `templates/scoring.py`

### Outputs the scoring program must write

- **`/app/output/scores.json`** — required. A flat JSON object whose
  keys match the leaderboard column keys. See §9.
- **`/app/output/detailed_results.html`** — optional. Only consumed if
  `enable_detailed_results: True` is set on the competition. The
  platform polls this file and live-streams updates to the frontend.

The scoring program **must** exit 0 on success. A non-zero exit marks
the submission as failed regardless of whether `scores.json` was
written.

---

## 8. Ingestion program contract

Use an ingestion program when participants submit **code** rather than
predictions. The ingestion program runs the participant's code against
the task's input data to produce predictions, which the scoring program
then grades.

### `metadata.yaml`

Quoted verbatim from `Competition-Bundle-Structure.md`:

```yaml title="metadata.yaml"
command: python3 /app/program/ingestion.py /app/input_data/ /app/output/ /app/program /app/ingested_program
```

The positional args by convention:

1. `/app/input_data/` — task input data
2. `/app/output/` — where the ingestion program writes predictions
3. `/app/program/` — its own scripts directory
4. `/app/ingested_program/` — the participant's unpacked submission

Passing these as args is **optional** but conventional; the script can also just hardcode them.

*NOTE:* use two sets of paths when creating ingestion program, set 1 to be used by Codabench, set 2 to be used locally for testing the scripts. To check an ingestion template example, see `templates/ingestion.py`

### Filesystem the ingestion step sees

| path                    | purpose                                                                                |
|-------------------------|----------------------------------------------------------------------------------------|
| `/app/input_data/`      | Task input data.                                                                       |
| `/app/program/`         | Ingestion program scripts.                                                             |
| `/app/ingested_program/`| Participant's submission code (only available during ingestion).                       |
| `/app/output/`          | Where to write predictions. Becomes `/app/input/res/` for the scoring step.            |
| `/app/shared/`          | Only when `ingestion_only_during_scoring: True` on the task; shared with scoring.      |

### Code vs result vs dataset submission

- **Result submission** — phase has `accepts_only_result_submissions:
  True`. Participants upload a prediction file directly; no ingestion
  program runs, just the scoring program against `/app/input/res/`.
- **Code submission** — participants upload Python (or other) code.
  Bundle must include an `ingestion_program/`. The ingestion program
  invokes the participant code (mounted at `/app/ingested_program/`)
  and writes predictions to `/app/output/`.
- **Dataset submission** — variant of code submission where the
  organizer ships a fixed sample algorithm and participants upload a
  dataset. The ingestion program's `$input` and `$submission_program`
  paths are effectively swapped relative to a code-submission contest.
  See `Dataset-competition-creation-and-participate-instruction.md`.

---

## 9. `scores.json` ↔ leaderboard mapping

This is the single most common source of "competition uploaded but no
scores show up" bugs. The rule:

> **Every `key` under `leaderboards[].columns` MUST be a top-level key
> in the `scores.json` written to `/app/output/`** — except for
> columns with a `computation:` (those are derived by the platform).

### Leaderboard schema

Per leaderboard:

| key               | type    | required | notes                                                                                  |
|-------------------|---------|----------|----------------------------------------------------------------------------------------|
| `title`           | string  | yes      | Display title.                                                                         |
| `key`             | string  | yes      | Internal key.                                                                          |
| `columns`         | list    | yes      | At least one column.                                                                   |
| `submission_rule` | string  | no       | `Add`, `Add_And_Delete`, `Add_And_Delete_Multiple`, `Force_Last`, `Force_Latest_Multiple`, `Force_Best`. |
| `hidden`          | bool    | no       | Hide leaderboard from non-admins.                                                      |

Per column:

| key                   | type     | required | notes                                                                                              |
|-----------------------|----------|----------|----------------------------------------------------------------------------------------------------|
| `title`               | string   | yes      | Column header.                                                                                     |
| `key`                 | string   | yes      | **Must match a key in `scores.json`** (unless `computation` is set).                               |
| `index`               | int      | yes      | Column order (left-to-right).                                                                      |
| `sorting`             | string   | no       | `asc` (smaller = better) or `desc` (larger = better).                                              |
| `computation`         | string   | no       | One of `sum`, `avg`, `min`, `max`. **Do not write scores for computation columns** — the platform derives them. |
| `computation_indexes` | list[int]| with `computation` | Indexes of the columns to combine.                                                                 |
| `precision`           | int      | no       | Decimal digits (default `2`).                                                                      |
| `hidden`              | bool     | no       | Hide column.                                                                                       |

### Concrete YAML ↔ JSON pair

YAML (verbatim from `Yaml-Structure.md`):

```yaml
leaderboards:
  - title: Results
    key: main
    submission_rule: "Force_Last"
    columns:
      - title: Accuracy Score 1
        key: accuracy_1
        index: 0
        sorting: desc
        precision: 2
        hidden: False
      - title: Accuracy Score 2
        key: accuracy_2
        index: 1
        sorting: desc
        precision: 3
        hidden: False
      - title: Max Accuracy
        key: max_accuracy
        index: 2
        sorting: desc
        computation: max
        precision: 3
        hidden: False
        computation_indexes:
          - 0
          - 1
      - title: Duration
        key: duration
        index: 3
        sorting: asc
        precision: 2
        hidden: False
```

Matching `scores.json` (verbatim from `Leaderboard-Functionality.md`):

```json
{"accuracy_1": 0.5, "accuracy_2": 0.75, "duration": 123.45}
```

Note `max_accuracy` is absent from JSON because it has `computation:
max`. Writing it would be ignored — the platform computes it from
columns 0 and 1 at read time.

### Lint rules to enforce in a bundle generator

For each leaderboard `L` and each column `C` in `L.columns`:

1. If `C.computation` is **unset**: `C.key` MUST appear as a key in
   every example `scores.json` produced by a smoke test of the scoring
   program.
2. If `C.computation` is **set**: `C.key` MUST NOT be written by the
   scoring program (it will be overwritten / ignored), and
   `C.computation_indexes` MUST reference existing column indexes in
   the same leaderboard.
3. `C.index` values within a leaderboard must be unique.
4. `sorting` must be `asc` or `desc` when present.
5. The primary column (index 0) determines ranking; subsequent columns
   tiebreak left-to-right, then by submission timestamp.

### Submission rules summary

| YAML value                  | behavior                                              |
|-----------------------------|-------------------------------------------------------|
| `Add`                       | One submission per participant on leaderboard.        |
| `Add_And_Delete`            | One submission, can be removed.                       |
| `Add_And_Delete_Multiple`   | Many submissions, all removable.                     |
| `Force_Last`                | Latest submission always replaces the previous one.   |
| `Force_Latest_Multiple`     | Multiple latest submissions forced on leaderboard.    |
| `Force_Best`                | Only best submission shown.                           |

---

## 10. Solutions

`solutions:` is a list of example/baseline submissions. They are used
as smoke-test inputs (the organizer can re-submit them) and as
starting-kit baselines.

| key      | type      | required | notes                                                       |
|----------|-----------|----------|-------------------------------------------------------------|
| `index`  | int       | yes      | Internal id.                                                |
| `tasks`  | list[int] | yes      | Indexes of tasks this solution applies to.                  |
| `path`   | path      | yes      | Path to `.zip` or directory containing the solution bundle. |

Example (verbatim from `Yaml-Structure.md`):

```yaml
solutions:
  - index: 0
    path: solutions/solution1.zip
    tasks:
    - 0
    - 1
  - index: 1
    path: solutions/solution2/
    tasks:
    - 0
```

A solution archive should contain the same files a real participant
would upload (a `submission.py` for code contests, or a predictions
file for result contests).

---

## 11. Image, terms, logo

### Logo (`image:`)

- Format: `.png` or `.jpg`.
- Path is relative to `competition.yaml`, e.g. `image: logo.png` or
  `image: images/pi.png`.
- File size limit: `# (docs unclear — verify on platform)` — the docs
  do not state a hard limit. Keep under ~1 MB to be safe.

### Terms (`terms:`)

- Path to a `.md` or `.html` file relative to `competition.yaml`.
- Conventionally also listed under `pages:` so it shows as a tab.
- This is the document participants must accept to join. Without it the
  bundle is invalid (it is a required top-level key).

### Where files live

There is no enforced subdirectory layout. The reference examples place
the logo at the bundle root (`logo.png`) or under `images/`, and pages
under `pages/`. Match whatever you write into `competition.yaml`.

### Fact sheet (per-submission metadata questionnaire)

Optional. JSON-style block under top-level `fact_sheet:` declaring
per-submission questions (`checkbox`, `text`, `select`). Each entry has
`key`, `type`, `title`, `selection`, `is_required`,
`is_on_leaderboard`. See `Yaml-Structure.md` for the full schema.

---

## 12. Zip layout (the upload-error trap)

> **The `.zip` MUST contain `competition.yaml` at its ROOT, not nested
> inside a folder.**

The single most common bundle-upload failure is "you zipped the folder
instead of its contents". From inside the bundle directory:

```sh
# CORRECT — competition.yaml ends up at the root of the zip
cd my_competition/
zip -r ../my_competition.zip .

# WRONG — produces my_competition/competition.yaml at the root
zip -r my_competition.zip my_competition/
```

To verify before uploading:

```sh
unzip -l my_competition.zip | head -20
# competition.yaml must appear with no leading directory.
```

The `Update-programs-or-data.md` doc reinforces this for sub-archives
too: *"Make sure to zip the files at the root of the archive, without
zipping the folder structure."*

Same rule applies to sub-archives you reference from
`competition.yaml`:

- `scoring_program.zip` must contain `metadata.yaml` at its root, not
  `scoring_program/metadata.yaml`.
- `ingestion_program.zip` likewise.
- `solutions/solution1.zip` must contain the solution files at its
  root.

---

## 13. Appendix: minimal valid bundle

A complete, copy-pasteable skeleton for a result-submission contest
(no ingestion program). Five files. Zip the contents of the directory
(not the directory) and upload.

```
minimal_bundle/
├── competition.yaml
├── pages/
│   └── overview.md
├── scoring_program/
│   ├── metadata.yaml
│   └── score.py
└── reference_data/
    └── truth.csv
```

### `competition.yaml`

```yaml
version: 2
title: Minimal Codabench Competition
description: A minimal but valid Codabench bundle skeleton.
image: pages/overview.md   # replace with a real logo.png in production
terms: pages/overview.md   # in production, point at a real terms.md

pages:
  - title: Overview
    file: pages/overview.md

phases:
  - index: 0
    name: Single Phase
    description: Submit your predictions
    start: 2025-01-01 00:00:00
    accepts_only_result_submissions: True
    tasks:
      - 0

tasks:
  - index: 0
    name: Default Task
    description: Compare predictions against reference
    scoring_program: scoring_program/
    reference_data: reference_data/

leaderboards:
  - title: Results
    key: main
    submission_rule: Force_Best
    columns:
      - title: Accuracy
        key: accuracy
        index: 0
        sorting: desc
        precision: 4
```

Note: `image:` and `terms:` are required top-level keys, so for a true
minimum-files skeleton above we point both at the overview markdown.
In a real bundle replace `image:` with a `logo.png` and `terms:` with
a dedicated `pages/terms.md`.

### `pages/overview.md`

```markdown
# Minimal Codabench Competition

This is a minimal bundle. Participants upload a `predictions.csv` with
the same row order as the reference data. The scoring program computes
accuracy.
```

### `scoring_program/metadata.yaml`

```yaml
command: python3 /app/program/score.py /app/input/ /app/output/
```

### `scoring_program/score.py`

```python
#!/usr/bin/env python3
"""Minimal scoring program for a result-submission Codabench task.

Reads:
  /app/input/ref/truth.csv         -- ground truth, one label per line
  /app/input/res/predictions.csv   -- participant predictions, one label per line

Writes:
  /app/output/scores.json          -- {"accuracy": <float>}
"""
import json
import os
import sys


def read_labels(path):
    with open(path, "r", encoding="utf-8") as f:
        return [line.strip() for line in f if line.strip()]


def main():
    input_dir = sys.argv[1] if len(sys.argv) > 1 else "/app/input/"
    output_dir = sys.argv[2] if len(sys.argv) > 2 else "/app/output/"

    ref_path = os.path.join(input_dir, "ref", "truth.csv")
    pred_path = os.path.join(input_dir, "res", "predictions.csv")

    truth = read_labels(ref_path)
    preds = read_labels(pred_path)

    if len(truth) != len(preds):
        raise SystemExit(
            f"Prediction length {len(preds)} != reference length {len(truth)}"
        )

    correct = sum(int(t == p) for t, p in zip(truth, preds))
    accuracy = correct / len(truth) if truth else 0.0

    os.makedirs(output_dir, exist_ok=True)
    with open(os.path.join(output_dir, "scores.json"), "w", encoding="utf-8") as f:
        json.dump({"accuracy": accuracy}, f)


if __name__ == "__main__":
    main()
```

### `reference_data/truth.csv`

```
0
1
1
0
1
```

### Smoke-test contract

For this skeleton to round-trip cleanly:
- `competition.yaml` parses as YAML and contains all required keys.
- `scoring_program/metadata.yaml` `command:` invokes a script that
  exists in the same directory.
- The script writes `scores.json` to `/app/output/`.
- Every non-computation `columns[].key` is a top-level key in
  `scores.json` — here, `accuracy` is in both.
- The directory contents (NOT the directory itself) are zipped so
  `competition.yaml` lives at the zip root.

---

## Source map

Primary sources used for the schema above:

- `Organizers/Benchmark_Creation/Yaml-Structure.md` — top-level keys,
  pages, phases, tasks, solutions, leaderboards, fact_sheet.
- `Organizers/Benchmark_Creation/Competition-Bundle-Structure.md` —
  bundle layout, scoring/ingestion `metadata.yaml`, `scores.json`
  shape.
- `Organizers/Benchmark_Creation/Leaderboard-Functionality.md` —
  submission rules, computation columns, primary-column ranking, the
  YAML↔JSON pair.
- `Organizers/Benchmark_Creation/Detailed-Results-and-Visualizations.md`
  — `enable_detailed_results`, scoring program path conventions.
- `Organizers/Benchmark_Creation/Competition-docker-image.md` —
  `docker_image:` defaults.
- `Organizers/Benchmark_Creation/Dataset-competition-creation-and-participate-instruction.md`
  — dataset-submission variant.
- `Organizers/Running_a_benchmark/Update-programs-or-data.md` — sub-zip
  layout rule ("zip files at root of archive").
- `Developers_and_Administrators/Submission-Docker-Container-Layout.md`
  — `/app/*` paths on the worker.
- `Developers_and_Administrators/Submission-Process-Overview.md` —
  high-level submission lifecycle.
