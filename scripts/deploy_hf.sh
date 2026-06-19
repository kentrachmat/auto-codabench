#!/usr/bin/env bash
#
# Deploy the web UI to the Hugging Face Space.
#
# GitHub `master` is the SINGLE source of truth; the Space only ever RECEIVES
# `master`. The catch is that the Space's README needs a YAML config header
# (sdk: docker, app_port, …) that `master`'s README deliberately does NOT carry.
# So this script reconstructs the deploy content as "<src-ref> + the HF header
# on README.md" inside an isolated worktree and force-pushes it to the Space's
# `main`. That keeps GitHub and the Space from ever drifting on the README, and
# means you never hand-merge or hand-force-push to the `hf` remote.
#
# Usage:
#   scripts/deploy_hf.sh                 # deploy origin/master (prompts before push)
#   scripts/deploy_hf.sh --yes           # no prompt (for automation)
#   scripts/deploy_hf.sh --dry-run       # build + show the diff, do NOT push
#   scripts/deploy_hf.sh <src-ref>       # deploy a specific ref instead of origin/master
#
# Env:
#   HF_REMOTE   git remote name for the Space (default: hf)
#
set -euo pipefail

HF_REMOTE="${HF_REMOTE:-hf}"
ASSUME_YES=0
DRY_RUN=0
SRC_REF="origin/master"

for arg in "$@"; do
  case "$arg" in
    --yes|-y)   ASSUME_YES=1 ;;
    --dry-run)  DRY_RUN=1 ;;
    -h|--help)  sed -n '2,28p' "$0"; exit 0 ;;
    -*)         echo "unknown flag: $arg" >&2; exit 2 ;;
    *)          SRC_REF="$arg" ;;
  esac
done

cd "$(git rev-parse --show-toplevel)"

git remote get-url "$HF_REMOTE" >/dev/null 2>&1 || {
  echo "error: git remote '$HF_REMOTE' not found. Add it with:" >&2
  echo "  git remote add $HF_REMOTE https://huggingface.co/spaces/<user>/<space>" >&2
  exit 1
}

echo "==> fetching origin + $HF_REMOTE"
git fetch -q origin
git fetch -q "$HF_REMOTE"

git rev-parse --verify -q "$SRC_REF^{commit}" >/dev/null || {
  echo "error: ref '$SRC_REF' not found" >&2; exit 1
}
SRC_SHA="$(git rev-parse --short "$SRC_REF")"

# Build the deploy commit in a throwaway worktree so the user's checkout and
# branch are never touched.
TMP="$(mktemp -d)"
cleanup() { git worktree remove --force "$TMP" >/dev/null 2>&1 || true; rm -rf "$TMP"; }
trap cleanup EXIT

echo "==> staging $SRC_REF ($SRC_SHA) in a temp worktree"
git worktree add -q --detach "$TMP" "$SRC_REF"

# Inject the HF Spaces config header into README.md (single source of truth,
# below). Idempotent: any pre-existing leading frontmatter is replaced.
python3 - "$TMP/README.md" <<'PY'
import re, sys
path = sys.argv[1]
HEADER = """---
title: AutoCodabench
emoji: 🧪
colorFrom: indigo
colorTo: blue
sdk: docker
app_port: 7860
pinned: false
license: mit
short_description: Chat assistant for designing Codabench competitions.
---

"""
try:
    body = open(path, encoding="utf-8").read()
except FileNotFoundError:
    body = ""
m = re.match(r"^---\n.*?\n---\n+", body, flags=re.S)  # strip existing frontmatter
if m:
    body = body[m.end():]
open(path, "w", encoding="utf-8").write(HEADER + body)
print("  README.md: HF Space header injected")
PY

git -C "$TMP" add README.md
git -C "$TMP" commit -q -m "Deploy to HF Space: $SRC_REF ($SRC_SHA) + Space metadata header"

echo
echo "==> changes vs current $HF_REMOTE/main:"
git -C "$TMP" --no-pager diff --stat "$HF_REMOTE/main..HEAD" || true
echo

if [ "$DRY_RUN" -eq 1 ]; then
  echo "==> --dry-run: not pushing. (Deploy commit lives only in the temp worktree.)"
  exit 0
fi

if [ "$ASSUME_YES" -ne 1 ]; then
  if [ -t 0 ]; then
    read -r -p "Force-push this to $HF_REMOTE main (LIVE deploy)? [y/N] " ans
    case "$ans" in y|Y|yes|YES) ;; *) echo "aborted."; exit 0 ;; esac
  else
    echo "error: refusing to deploy non-interactively without --yes" >&2
    exit 1
  fi
fi

echo "==> deploying to $HF_REMOTE main"
git -C "$TMP" push --force "$HF_REMOTE" HEAD:main
echo "==> done — the Space will rebuild its Docker image. Watch the Space's Build/Logs tab."
