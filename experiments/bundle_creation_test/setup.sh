#!/usr/bin/env bash
# Symlink the experiment's orchestrator skill, the packaged
# skills it shells out to, and the experiment's one in-process subagent
# definition into .claude/ so Claude Code picks them up under its
# standard discovery paths. Idempotent.
#
# .claude/ itself is gitignored — the source of truth for these skills
# lives in experiments/bundle_creation_test/ and src/autocodabench/skills/.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
cd "${REPO_ROOT}"

mkdir -p .claude/agents .claude/skills

echo "=== linking agent definitions into .claude/agents/ ==="
# Only one in-process subagent now (the log auditor). Phases 2/3/4a
# are shell-outs, not subagents, and their definitions live in
# src/autocodabench/skills/ as skills rather than agents.
for f in experiments/bundle_creation_test/agents/*.md; do
    name="$(basename "$f")"
    target="../../experiments/bundle_creation_test/agents/${name}"
    link=".claude/agents/${name}"
    ln -sfn "${target}" "${link}"
    printf "  %-44s -> %s\n" "${link}" "${target}"
done

echo
echo "=== sweeping stale agent symlinks ==="
# Without this, removing a tracked agent file leaves a dangling symlink.
for link in .claude/agents/*.md; do
    [ -L "$link" ] || continue
    target_abs="$(readlink "$link" 2>/dev/null || true)"
    [ -e ".claude/agents/${target_abs}" ] || {
        echo "  removing stale symlink: ${link} -> ${target_abs}"
        rm -f "${link}"
    }
done

echo
echo "=== linking skills into .claude/skills/ ==="
# Five skills the experiment depends on:
#   - bundle-creation-test:           the orchestrator (this experiment owns it)
#   - autocodabench-plan:             phase 2 shell-out target (packaged skill)
#   - autocodabench-implement:        phase 3 shell-out target (packaged skill)
#   - autocodabench-reformat-and-run: phase 4a shell-out target (packaged skill)
#   - codabench-bundle:               schema reference loaded by implement
#   - competition-design:             design reference loaded by plan
SKILLS=(
    "bundle-creation-test:experiments/bundle_creation_test/skills/bundle-creation-test"
    "autocodabench-plan:src/autocodabench/skills/plan"
    "autocodabench-implement:src/autocodabench/skills/autocodabench-implement"
    "autocodabench-reformat-and-run:src/autocodabench/skills/autocodabench-reformat-and-run"
    "codabench-bundle:src/autocodabench/skills/codabench-bundle"
    "competition-design:src/autocodabench/skills/competition-design"
)
for entry in "${SKILLS[@]}"; do
    skill_name="${entry%%:*}"
    src="${entry#*:}"
    target="../../${src}"
    link=".claude/skills/${skill_name}"
    if [[ ! -d "${src}" ]]; then
        echo "  SKIP ${skill_name}: ${src} not found on disk"
        continue
    fi
    if [[ -L "${link}" ]] && [[ "$(readlink "${link}" 2>/dev/null || true)" == "${target}" ]]; then
        printf "  %-44s (already linked)\n" "${link}"
        continue
    fi
    ln -sfn "${target}" "${link}"
    printf "  %-44s -> %s\n" "${link}" "${target}"
done

echo
echo "=== done ==="
echo "Verify with:  ls -la .claude/agents/ .claude/skills/"
echo
echo "Then in a top-level Claude Code session, ask:"
echo '  "Run the bundle-creation experiment on <competition_sample_name>"'
echo
echo "The orchestrator (bundle-creation-test skill) will:"
echo "  1. Compute run_id, create runs/<comp>/<run_id>/, snapshot expected_results."
echo "  2. claude --print /autocodabench-plan ...          → specs/implementation_plan.md"
echo "  3. claude --print /autocodabench-implement ...     → bundles/<slug>/ (validated + runtime-tested)"
echo "  4. for each sub_N:"
echo "       claude --print /autocodabench-reformat-and-run ...  → reformat_run/sub_N/final.json"
echo "       Task submission-log-auditor ...                     → log_audit/sub_N/verdict.json"
echo "  5. Aggregate missing-info → missing_info_report.json"
echo "  6. Finalize manifest, remove conda env, write run_report.md"
