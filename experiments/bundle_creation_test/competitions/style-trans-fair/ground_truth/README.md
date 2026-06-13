# `ground_truth/` — for human review only, NEVER read by any agent

This folder holds the canonical Codabench reference for the
**STYLE-TRANS-FAIR** competition. It exists so a maintainer can compare
what AutoCodabench's agents produce against the real bundle and the real
submission scores. Every agent in
[`../../../agents/`](../../../agents/) is denied read access here by
`allowedTools` + `permissionMode: dontAsk` — exposing this to the agents
would let them either copy the golden bundle wholesale (defeating the
test) or peek at expected scores (defeating the comparison).

## Layout

```
ground_truth/
├── README.md                # this file (tracked)
├── bundle/                  # the canonical Codabench bundle (gitignored, populate from upstream)
│   ├── .gitignore           # keeps the dir alive in git, hides the contents
│   ├── competition.yaml
│   ├── scoring_program/
│   ├── ingestion_program/
│   ├── reference_data/
│   ├── sample_data/
│   ├── images/
│   └── ... (~7 MB across ~850 files of images / data / scoring code)
└── sample_submissions/      # tracked source for the per-sub run/score loop
    └── sub_<N>/
        ├── submission/      # the actual code that ran on Codabench (small, tracked)
        │   ├── model.py
        │   └── metadata
        └── expected_result.json   # the score this submission produced (small, tracked)
```

## What's tracked vs. what's not

- **Tracked** (committed in this repo): this README, every
  `sample_submissions/sub_<N>/` (the submission source code + its
  expected_result.json). These files are small text — under 50 KB
  per sub — and they are the **specification** for step 5 of the
  experiment.
- **Not tracked** (gitignored, fetch separately): `bundle/**`. The
  golden bundle is ~7 MB of images + reference data + scoring code.
  Re-downloading it per machine is faster than carrying it in git.

## Populating `bundle/`

The canonical bundle lives in the upstream repo
**https://github.com/fnachalearn/style-trans-fair**. Clone it to a
temp dir, copy the contents into `bundle/`, and discard the clone:

```bash
# from the repo root
TMP=$(mktemp -d)
git clone --depth 1 https://github.com/fnachalearn/style-trans-fair "$TMP"
# the upstream repo root IS the bundle root — copy everything except .git
rsync -a --exclude='.git' "$TMP/" \
  experiments/bundle_creation_test/competitions/style-trans-fair/ground_truth/bundle/
rm -rf "$TMP"
```

After populating you should see ~7 MB across ~850 files. Verify with:

```bash
du -sh experiments/bundle_creation_test/competitions/style-trans-fair/ground_truth/bundle/
ls experiments/bundle_creation_test/competitions/style-trans-fair/ground_truth/bundle/
# expect: competition.yaml, scoring_program/, ingestion_program/,
#         reference_data/, sample_data/, images/, etc.
```

If the upstream repo's layout differs from "root = bundle", read its
README and adjust the rsync source path accordingly — the goal is just
`bundle/competition.yaml` present + the standard Codabench subdirs.

Once populated, the orchestrator never touches it (no agent has read
permission); the bundle sits here purely for human comparison against
what the agents produce under `<run_id>/bundle/`.

## Populating `sample_submissions/`

`sub_1/` is already tracked in this repo with the submission code and
its expected_result.json. Additional `sub_N/` subdirs follow the same
shape — each must have a `submission/` subdir (the code) and an
`expected_result.json` file (the score that submission produced when
run against the real Codabench instance), with the schema documented
in [`../../../README.md`](../../../README.md#manifest-schema).

If `sample_submissions/sub_1/` is missing on your clone, switch to the
`experiment_test-bundle-creation` branch (it lives only there, not on
master) and the files reappear via the normal tracked-file restore.
