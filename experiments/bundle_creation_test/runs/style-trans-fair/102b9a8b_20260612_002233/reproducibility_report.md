# Reproducibility report — STYLE-TRANS-FAIR

**Question under test:** given only the competition's *inputs* (the
proposal PDF + sample data under `competitions/style-trans-fair/input/`),
how closely does the autocodabench pipeline reproduce the *human-built
production competition* (`ground_truth/bundle/`, which ran publicly as
Codabench competition #601) — and how many of the system's tests does the
result pass?

**Run:** `102b9a8b_20260612_002233` · branch `jmlr-oss-direction` ·
backbone: Claude (subscription auth)

# Method and provenance

The generation pipeline ran fully **blind** under the harness's leakage
protocol: the planner saw only `input/`; the implementer saw only the
locked plan + `input/sample_data/`; nobody who wrote the bundle ever saw
`ground_truth/**`. The side-by-side comparison below was performed by
the orchestrator **after** the run completed and the manifest was
finalized, at the operator's explicit request — i.e. it is the "human
comparison" step the protocol reserves, not part of the experiment, and
it could not have influenced generation.

---

## 1. Pipeline outcome (which tests passed)

| Test / oracle                                 | Result  | Detail                                                                                                                                  |
| --------------------------------------------- | ------- | --------------------------------------------------------------------------------------------------------------------------------------- |
| Preconditions                                 | ✅ pass | 1 ground-truth submission, expected scores parse                                                                                        |
| Plan completeness (7 design sections)         | ✅ pass | 7/7 sections, 7 assumptions documented, 17 turns                                                                                        |
| Structural validity (`validate_bundle`)     | ✅ pass | clean after the implementer's own lint-fix loop                                                                                         |
| **Baseline execution oracle**           | ✅ pass | bundle's own baseline through ingestion+scoring, attempt 3/5; scores produced for all 3 leaderboard keys (gm=0.0122, am=0.344, wga=0.0) |
| **Starting-kit notebook oracle**        | ✅ pass | full execution, attempt 4/4                                                                                                             |
| Bundle zip produced                           | ✅ pass | `bundles/style-trans-fair.zip`                                                                                                        |
| **Ground-truth score fidelity (sub_1)** | ❌ fail | no score produced — see §3                                                                                                            |
| Validator, deterministic tier (post-hoc)      | ✅ pass | 0 gate failures, 2 advisory findings                                                                                                    |
| Validator, judged tier (post-hoc)             | —      | 1 advisory finding (real, subtle: a "leaderboard revealed at close" promise with no enforcing config flag)                              |

**4 of the 5 harness phases passed; the run fails honestly at
`fail_at_score_submissions/sub_1`.** The two failure causes are
characterized exactly in §3 — one infrastructural, one semantic — and
neither was recoverable under the harness's no-retry rule.

## 2. Design reproduction: generated vs. ground truth

Dimension-by-dimension comparison against the production bundle. ✅ =
reproduced, ◐ = reproduced with deviation, ❌ = diverged.

| Dimension                      | Ground truth (production)                                                           | Generated (blind)                                                               | Verdict                                                                  |
| ------------------------------ | ----------------------------------------------------------------------------------- | ------------------------------------------------------------------------------- | ------------------------------------------------------------------------ |
| Task framing                   | bias-invariant classification of style-transferred images, category×style confound | same, including the confound framing in pages                                   | ✅                                                                       |
| Protocol                       | γ (code submission: ingestion + scoring)                                           | γ (ingestion + scoring)                                                        | ✅                                                                       |
| Data realism                   | real stylized images                                                                | real stylized images from `input/sample_data/` — **no synthetic data** | ✅                                                                       |
| Test set size                  | 180 (set 2)                                                                         | **180** (`reference_data/test_labels.csv`)                              | ✅                                                                       |
| Train set size                 | 90 (set 1)                                                                          | **90** (`input_data/train/`)                                            | ✅                                                                       |
| Group structure                | 9 per-(category×style) groups                                                      | 9 per-(CATEGORY×STYLE) groups                                                  | ✅                                                                       |
| Metric family                  | geometric mean of per-group accuracies                                              | geometric mean of per-group accuracies                                          | ✅                                                                       |
| **Metric zero-handling** | hard zero: any zero-accuracy group ⇒ score 0                                       | `gmean(accs + 1e-12)` ⇒ small positive score                                 | ❌ (quantified in §3)                                                   |
| Leaderboard columns            | `set2_score` only                                                                 | `gm` primary + `am`, `wga` secondaries, all `desc`                      | ◐ (richer; names differ)                                                |
| Train-set diagnostic score     | reported (`set1_train`)                                                           | not scored (test only)                                                          | ❌                                                                       |
| Phases                         | 2 (dev + final)                                                                     | 2 (dev + final)                                                                 | ✅                                                                       |
| Submission limits              | none enforced (pages*promise* limits the config doesn't enforce — see §4)       | dev: 5/day + 100 total; final: 1                                                | ◐ (generated is stricter than GT*and* matches GT's own stated intent) |
| Docker image                   | custom `ktgiahieu/codalab-legacy-with-tensorflow:3.8`                             | generic `codalab/codalab-legacy:py3`                                          | ❌ (consequential — §3)                                                |
| Baseline shipped               | `sample_code_submission/`                                                         | `solutions/solution_baseline/`                                                | ✅                                                                       |
| Starting-kit notebook          | `README.ipynb`                                                                    | `README.ipynb`                                                                | ✅                                                                       |
| Pages                          | overview/evaluation/data/files                                                      | overview/evaluation/data/terms                                                  | ✅                                                                       |

**Summary: 11 ✅, 2 ◐, 3 ❌ across 16 dimensions.** The blind pipeline
reproduced the competition's identity — task, protocol, real data with
*exactly* matching 180/90 splits, 9-group structure, geometric-mean
metric family, two-phase schedule, working baseline and notebook. The
three divergences are specific and instructive rather than random: a
metric edge-case semantic, the absence of a train-set diagnostic score,
and the docker/runtime pin.

## 3. Score fidelity — why it failed, twice over

**Cause 1 (what actually happened): infrastructure.** The ground-truth
submission trains a TF/Keras model. In the per-run conda env its
training stalled after model construction — the TF/pyarrow
native-deadlock class already documented in the harness README as
unrecoverable by code edits. The reformat agent burned its session
bisecting the hang (import-order probes are in `session.jsonl`) and
exited without a result; the auditor verdicted `no_score_produced`.
Notably, the production competition pinned a **custom TF docker image**
— the GT author had evidently solved this same environment problem at
the docker layer, which the generated bundle's generic image pin did
not.

**Cause 2 (counterfactual, found in post-hoc analysis): metric
semantics.** Even on a healthy env, the expected score would likely not
have matched within tolerance. The GT metric returns a hard **0.0**
whenever any group accuracy is exactly 0 (sub_1's set2 has one such
group). The generated scorer computes `gmean(accs + 1e-12)`, a
numerically-stabilized variant. On sub_1's recorded per-group
accuracies, that yields **0.0174** against an expected **0.0 ± 0.001**
— outside tolerance by 17×. The proposal did not specify zero-handling;
the implementer made a reasonable-but-different choice. This is
precisely the class of silent design divergence the missing-info
inventory exists to surface (and didn't — see §5, defect 2).

## 4. The validator examined both bundles (post-hoc)

Same checks, both artifacts, judged tier included:

|                                             | Gates failed | Findings (advisory) | Passes |
| ------------------------------------------- | ------------ | ------------------- | ------ |
| **Generated** bundle                  | 0            | 3                   | 9      |
| **Ground-truth** bundle (production!) | 0¹          | 7                   | 5      |

¹ After a validator fix this comparison itself forced: the GT bundle
uses the legacy extensionless `metadata` filename, which production
Codabench accepts but our schema gate rejected — a false positive,
fixed and regression-tested during this analysis
(`test_validate_accepts_legacy_metadata_filename`).

The judged tier found **real defects in the production competition**:
its Development-Phase page promises "at most 5 submissions per day" and
its Final-Phase page promises "submit only once", while the production
config enforces **no limits at all** — exactly the pages↔config
contradiction class the check was designed for, in a competition that
ran publicly. The generated bundle, built with the competition-design
knowledge skill in the loop, *enforces* the limits the original only
promised.

This is the strongest single result of the run: **the generated bundle
is measurably more best-practice-compliant than the human production
original it was blindly reproducing** (3 advisory findings vs 7, with
the GT's findings including unenforced submission limits, no shipped
baseline under `solutions/`, and the doc/config contradictions).

## 5. Defects discovered (each one actionable)

1. **Harness:** the reformat-and-run shell-out backgrounded its work and
   "armed monitors" — fatal in `claude --print` (the session ends,
   orphaning the run). Fix: the skill must require blocking on
   `run_user_submission` and forbid backgrounding/monitors.
2. **Harness:** neither shell-out wrote `missing_info_inventory.json`
   despite reporting gap counts (7 + 4) in their payloads. The metric
   zero-handling divergence (§3, cause 2) is exactly what that
   inventory should have flagged as `would_block_correct_scoring`.
   Fix: enforce inventory emission in the skill bodies.
3. **Environment:** TF/pyarrow native deadlock reproduced; mitigation
   path is bundle-level runtime pinning (the GT's custom docker image
   is the existence proof) — a candidate new deterministic check:
   "TF-dependent submission interface ⇒ TF-capable docker image".
4. **Validator (fixed in this run):** legacy `metadata` filename false
   positive — corrected against production ground truth + regression
   test added.

## 6. Verdict

On the reproducibility question: **the blind pipeline reproduced the
competition's design to a degree that surprised us** — identical data
splits (180/90), identical group structure, the right metric family,
the right protocol, and a runtime-validated bundle, from nothing but
the proposal PDF and sample data, for ~$21. It simultaneously
**failed the strictest test honestly** (ground-truth score fidelity:
0/1), with both failure causes isolated, characterized, and convertible
into concrete fixes — an environment-pinning gap and a metric edge-case
ambiguity that the proposal itself never resolved.

The run also demonstrated the validator earning its keep in both
directions: catching real defects in a production competition, and
being itself falsified (and fixed) by production ground truth.

**Recommendations:** (R1) fix harness defects 1–2 before the next run;
(R2) add the runtime-pinning check (defect 3); (R3) require zero-/
edge-case semantics of the primary metric in the plan template (closes
§3 cause 2 at the source); (R4) rerun this competition after R1–R3 —
the score-fidelity test is then expected to be reachable; (R5) treat
this report's §2 table as the per-competition template for the E1
campaign; (R6) the per-(backbone) repetition of this run is axis B of
`experiments/backbone_bench/`.
