# backbone_bench: A Comparison of LLM Backbones on the autocodabench Tasks

The backend seam (`autocodabench.backends`) makes the underlying language
model a measured variable. This experiment benchmarks LLM backbones on the
two tasks that the software delegates to them, using the repository's
ground-truth instruments as oracles. The comparison is therefore
commensurable by construction: every backbone is evaluated against the same
tool surface, the same audit-trail format, and the same pass/fail criteria.

## Axis A — Validation and Judging Quality (runnable now)

Axis A measures how reliably a backbone detects competition defects.

The script `run_judge_bench.py` seeds known authoring defects into otherwise
clean bundles (rebuilt deterministically from the replay fixture), runs the
validator, and measures the per-defect catch rate together with the
false-positive rate on clean copies. The deterministic tier is
backbone-independent and serves as the sanity baseline; because it never
invokes the model, it must achieve 9/9 for any backbone. The LLM-judged tier
constitutes the backbone-sensitive measurement.

```bash
# sanity baseline (no LLM at all)
python experiments/backbone_bench/run_judge_bench.py

# per backbone, ≥3 runs because the judged tier is stochastic
python experiments/backbone_bench/run_judge_bench.py --backend claude --runs 3
python experiments/backbone_bench/run_judge_bench.py --backend ollama:llama3.1 --runs 3
python experiments/backbone_bench/run_judge_bench.py --backend openai:gpt-4o-mini --runs 3
```

Results are written under `results/<backbone>/results.{json,md}`. The defect
library comprises 9 deterministic-tier targets and 3 judged-tier targets
(pages↔config contradictions in submission caps, metric direction, and phase
dates). The library is extended by appending entries to `DEFECTS`.

## Axis B — Bundle-Creation Quality (protocol)

Axis B measures how reliably a backbone authors a working competition.

The instrument is the existing ground-truth harness
(`experiments/bundle_creation_test/`), executed once per backbone per
competition with its blinding rules unchanged. For each (backbone,
competition, run) triple, the harness manifest already records every outcome
column; the measures and their sources are summarized in the following
table.

| Measure | Source |
|---|---|
| plan completeness (7 sections) | plan phase payload |
| structural validity | `validate_bundle` |
| runtime validity + attempts used (baseline ≤5, notebook ≤4) | implement phase payload |
| **score fidelity**: generated bundle scores the ground-truth submission within `expected_result.json` tolerance | log-audit verdicts |
| cost + turns | session results |

For each backbone, we report the success rate per stage over at least 3 runs
across N competitions, the distributions of attempts to convergence, score
deltas, and cost. The competition `style-trans-fair` is the first
ground-truth competition; the protocol scales by adding further competitions
under `experiments/bundle_creation_test/competitions/`.

Two caveats apply to non-Claude backbones on axis B. First, the generic
backend's plan phase cannot read PDF proposals and accepts text or markdown
proposals only. Second, the backbone must support native tool calling. Both
limitations are recorded as conditions of the run rather than silently
worked around.

## Reporting Standards

Reporting follows the standards used throughout the project
(`docs/scientific-validation.md` §4): model identifiers are pinned per run,
each stochastic condition is executed at least 3 times, dispersion is
reported, costs and tokens are reported, and raw logs are retained.
