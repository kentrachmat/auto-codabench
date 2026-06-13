# `sample_data/` — public dataset for STYLE-TRANS-FAIR

The public sample dataset the AutoCodabench agents work with during this
experiment run. Accessible to the **bundle-planner**, **bundle-implementer**,
and **submission-runner** agents — never to the reformatter (which only
sees the bundle interface + the ground-truth submission).

## What goes here

The dataset is ~3 MB across ~800 files of images and metadata:

```
sample_data/
├── README.md        # this file (tracked)
├── .gitignore       # keeps the dir alive in git, hides the rest (tracked)
├── info.json        # small dataset manifest
├── content/         # original images (one .jpg per item)
├── styles/          # style-reference images
├── stylized/        # the style-transferred (biased) training images
└── tasks/           # multi-task labels
```

None of those subdirs are tracked — they are bulk image data that any
clone can re-fetch in seconds from the upstream repo.

## Populating

The dataset ships with the upstream Codabench bundle source repo
**https://github.com/fnachalearn/style-trans-fair**. Clone it to a temp
dir, copy `sample_data/` contents into here, and discard the clone:

```bash
# from the repo root
TMP=$(mktemp -d)
git clone --depth 1 https://github.com/fnachalearn/style-trans-fair "$TMP"
# the upstream's sample_data/ lives at the repo root (it IS the bundle root,
# and the bundle includes sample_data/ for participants to download)
rsync -a --exclude='.git' "$TMP/sample_data/" \
  experiments/bundle_creation_test/competitions/style-trans-fair/input/sample_data/
rm -rf "$TMP"
```

After populating you should see something like:

```bash
$ du -sh experiments/bundle_creation_test/competitions/style-trans-fair/input/sample_data/
3.2M    .../sample_data/
$ ls experiments/bundle_creation_test/competitions/style-trans-fair/input/sample_data/
README.md  .gitignore  info.json  content/  styles/  stylized/  tasks/
```

If the upstream's `sample_data/` lives at a different path inside the
upstream repo (e.g. `bundle/sample_data/`), adjust the rsync source
accordingly — the goal is to land `info.json` + the four image
subdirs in this folder.

## Why it's not in git

These are derived image assets. Carrying ~800 files in git history per
clone wastes bandwidth and bloats the repo, when the upstream already
hosts the authoritative copy and a fresh re-fetch is one command. The
`.gitignore` next to this README ignores everything in the directory
except itself and this README, so the dir stays present in git for
agent path resolution.
