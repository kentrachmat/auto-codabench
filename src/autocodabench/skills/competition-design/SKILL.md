---
name: competition-design
description: Best practices for designing an AI competition — task framing, metrics, datasets, baselines, leaderboards, anti-cheating. Use when proposing or critiquing a competition idea.
---

# Competition design

Source: **Pavão et al., *AI Competitions and Benchmarks: The Science Behind the Contests* (2024).** Chapter cites point to that book. This is the canonical, quotable source — when in doubt, cite it.

Use this as a decision tree. Resolve every section in order before launch — most failed competitions died at step 1 or 2, not the leaderboard.

---

## 0a. Live tensions in the competition-design literature

Surface these to the user when they hit. They are the dimensions on
which thoughtful designers disagree, which makes them the most useful
Phase A conversation accelerators.

| Tension | Sides | Where to read | Why it matters for the design |
|---------|-------|---------------|-------------------------------|
| **Fixed vs rolling test sets** | Fixed test set is stable & comparable; rolling test set defeats memorisation by frontier models. | Pavão et al. (Ch. 3 §3.4); Roelofs et al. on benchmark drift | A NeurIPS reviewer will ask which you chose and why. |
| **λ result-submission vs γ code-submission** | λ is low-effort & community-friendly; γ is the only protocol that guarantees reproducibility and supports private test data. | Pavão et al. (Ch. 2 §2.4, Ch. 11 §11.2, Ch. 12 §12.1) | Decides whether you need a compute-worker, an ingestion program, and how anti-cheating works. |
| **Multi-task aggregation: Borda vs mean-of-scores** | Mean of normalised scores is interpretable but vulnerable to outliers; average-rank (Borda) is robust but discards magnitude. The book is opinionated *for* Borda. | Pavão et al. (Ch. 5 §5.6); Gibbard's theorem | Decides leaderboard ranking computation and what tie-breaking rule you write into competition.yaml. |
| **Public-leaderboard size: small vs large fraction of test set** | Small public leaderboard reduces overfitting risk but reduces feedback signal; large public leaderboard improves participant engagement but degrades into Goodharting. | Pavão et al. (Ch. 5 §5.1); Roelofs et al.; "Ladder" leaderboard (Blum & Hardt 2015) | Decides the public/private split ratio and the maximum daily submissions cap. |
| **Adversarial-accuracy metric direction** | For privacy/utility evaluation, ideal value is **0.5** (indistinguishable from real), NOT 1.0. Easy to set up wrong. | Pavão et al. (Ch. 4 §4.3) | If you mis-document this, participants optimize the wrong direction silently. |
| **Detection-is-possible vs detection-is-futile (AI-text)** | Sadasivan et al. (2023) argue detection accuracy → chance as generators improve; Krishna et al. (2023) + watermarking line argue this is escapable under specific assumptions. | `oa:W4382349837` vs `oa:W4385245221` | If your competition is on AI-text detection, you need to take a position in the motivation section. |
| **Single-objective vs multi-objective + weights** | Multi-objective leaderboards (accuracy + latency + fairness) better reflect real deployment but require an explicit aggregation rule declared at launch — otherwise winners are unfalsifiable. | Pavão et al. (Ch. 4 §4.4, Ch. 12 §12.4) | If you have a multi-objective scoring goal, the *aggregation rule* IS the metric. |
| **Open-generator vs closed-generator phases** | Holding back generators for the final phase (open-generator) measures *true* generalization; freezing the generator set (closed) is fairer to participants who optimized for the announced set. | Pavão et al. (Ch. 5 §5.4); SemEval-2024 Task 8 design notes | This is the strongest distribution-shift lever and the dimension on which AI-text benchmarks diverge. |
| **Code-sharing requirement: mandatory vs optional** | CTF (Donoho 2017) says winner-shares-code is one of the four pillars. Practice varies: some competitions require it for the prize, some leave it optional. | Pavão et al. (Ch. 1 §1.1, Ch. 13 §13.2) | Decides what you can include in the post-comp paper / shared task series and whether winning solutions are reusable. |
| **Pre-trained / foundation-model usage** | Allowing foundation models speeds up entry but turns the competition into prompt engineering; disallowing them tests algorithmic novelty but excludes large fraction of the field. | Pavão et al. (Ch. 12 §12.3) | Particularly hot for NLP / vision competitions; usually best declared in the rules section. |

Pattern for surfacing a tension in chat:

> There's a real tension here: **<Side A in one sentence>** [source A] vs.
> **<Side B in one sentence>** [source B]. The choice changes <which
> downstream artifact>. Which side do you lean toward, or do you want
> to design something that sidesteps it?

---

## 0. How to quote from this skill in user-facing chat

AutoCodabench's *scientific tone* rule requires that every proposal made
to the user names its source. When you draw on a bullet from this file,
**surface the chapter handle verbatim** in your reply.
The right citation form, depending on context:

- **Inline principle**: *"Per Pavão et al. (Ch. 4 §4.2) …"* — use when
  stating a single rule the user should believe.
- **End-of-claim parenthetical**: *"… distinct from the random-split
  baseline (Pavão et al., Ch. 3)."* — use when wrapping up an
  observation.
- **Mid-sentence handle only**: *"… the Common Task Framework (Ch. 1) …"*
  — acceptable for well-known terms-of-art the reader will recognise.

Never paraphrase a chapter title or invent a section number. When this
skill says `(Ch. 4)`, your user-facing text should say `(Ch. 4)` or
`Pavão et al. (Ch. 4)` — not "the metric chapter" or "chapter four of the
competitions book". Researchers will check; treat them like reviewers.

When the book and an empirical paper agree, **cite both**: the book for
the principle, the paper for the instance. Example:

> Per Pavão et al. (Ch. 5 §5.4), a hybrid λ→γ protocol is a defensible
> compromise; the M4 shared task adopted it (Wang et al., 2024,
> [oa:W4392055123]).

When you make a claim that goes beyond what this skill explicitly
states, prefix with **"Extrapolating from (Ch. X):"** so the user can
tell where the book ends and your inference begins.

---

## 1. Task framing

Decide if a competition is even the right tool, then nail down a single, well-posed question.

- **A competition is justified only when the task is hard, the metric is uncontroversial, and a community would gain from a shared benchmark** — otherwise run an internal evaluation (Ch. 1, Ch. 2). Competitions cost months; reuse an existing benchmark when one exists.
- **Apply the 5W taxonomy before writing any code: What is predicted, Why does it matter, How is it scored, Whether the data supports it, What For (downstream use)** — forces you to find scope holes early (Ch. 2).
- **Use the Common Task Framework (Donoho 2017): public dataset + agreed metric + leaderboard + winner-shares-code** — anything missing one of those four is not a CTF competition and will be hard to compare to prior work (Ch. 1, Ch. 2).
- **Pick exactly one primary metric and freeze it before launch** — multi-metric leaderboards without a tiebreak rule end in disputed wins (Ch. 4). Secondary metrics are reported, not ranked on.
- **Choose your protocol level explicitly: λ (result submission), α (labeled training), β (meta-training), γ (AutoML/code submission)** — each level changes what cheating looks like, how compute is bounded, and how you score (Ch. 2, Ch. 12).
- **Prefer code submission over result submission when (a) the test set is small, (b) the data is confidential, or (c) you want reproducibility** — result submission invites human-in-the-loop overfitting and label leakage (Ch. 11, Ch. 12).
- **Separate "intrinsic difficulty" (best possible model on infinite data) from "modeling difficulty" (gap between current SOTA and intrinsic limit)** — only the second is a useful competition (Ch. 2). If a logistic regression baseline gets 99%, there is no competition.
- **Write a falsifiable success criterion in the proposal: "we expect winning solutions to beat baseline X by Y on metric M with p<0.05"** — if you cannot state this, the task is not specified (Ch. 2).
- **Run a proposal review with 3+ external reviewers before announcing** — one of the 4 PILLARS of successful challenges; cheap to do and catches dead-end tasks (Ch. 2).
- **Decide single-objective vs multi-objective up front.** Multi-objective (e.g., accuracy + latency + fairness) needs an explicit aggregation rule (weighted sum, Pareto front, lexicographic) declared at launch (Ch. 4, Ch. 12).
- **Refuse to run a competition where the optimal strategy is data collection or label cleaning the organizer should have done** — that is outsourcing, not science.

---

## 2. Dataset design

The dataset is 80% of the work. Cutting corners here cannot be patched later.

- **Test set size rule of thumb: N ≈ 100 / E, where E is the anticipated error rate of top systems** — gives ~10% relative precision on the metric (Ch. 4). For E=1%, you need ~10k test examples.
- **Refined Guyon sizing formula for ranking k models: n ≈ (σ²/μ²) · k² · 10^(2v)** where v is the desired number of significant digits — use when you must distinguish many close submissions (Ch. 4).
- **Split granularity must match the unit of generalization the metric implies** ("voodoo machine learning" otherwise): patient-level data → split by patient, not by image; time series → split by time, never randomly (Ch. 3, Ch. 4). Random splits of grouped data are the #1 silent leakage source.
- **Hold out a private test set the participants never see, even via API** — public-test-only competitions overfit within weeks (Ch. 5, Ch. 11). Public leaderboard ≠ final ranking.
- **Use a final test set that was collected (or at minimum sequestered) AFTER the public set was released** — defeats Google-able and pre-trained-on-it labels (Ch. 3).
- **Audit for all three leakage categories before launch: (a) access to ground truth in features, (b) leakage intrinsic to data (e.g., duplicate patients), (c) leakage introduced in processing (normalizing using test statistics)** (Ch. 3). Train a model only on the leaked feature(s) — if it scores > baseline, you have a leak.
- **Publish a datasheet for the dataset (Gebru et al.) and a data nutrition label** — covers provenance, consent, known biases, intended use; required for credibility and increasingly for IRB/legal (Ch. 3).
- **Follow FAIR (Findable, Accessible, Interoperable, Reusable) and assign a persistent identifier (DOI)** — competitions whose data dies after the leaderboard close are not benchmarks (Ch. 3, Ch. 13).
- **For confidential data, choose between (a) synthetic surrogate trained with a generative model + privacy/utility evaluation, or (b) code submission against blind data on organizer-owned compute workers** (Ch. 12). Never release "de-identified" raw confidential data.
- **Minimum k=3 for k-anonymity; use differential privacy budget tracking when releasing aggregates** (Ch. 3, Ch. 12). Re-identification has won every adversarial competition that allowed it.
- **For adversarial-accuracy privacy evaluations the IDEAL value is 0.5 (indistinguishable from real), not 1.0** — counter-intuitive; document it in the metric spec or participants will optimize the wrong direction (Ch. 4).
- **Class imbalance: report per-class counts, choose imbalance-aware metric (AUROC, balanced accuracy, F1), and stratify the split** — accuracy on a 99/1 split rewards the constant predictor (Ch. 4).
- **Plan to release the data permanently under an explicit license at the end of the competition** — anonymous "research-only" zips disappear; benchmarks survive when re-usable (Ch. 3, Ch. 13).

---

## 3. Metric selection

Map task type → primary metric → secondary metrics. Defaults below; deviate only with a written reason.

| Task type | Primary metric | Secondary / sanity metrics | Notes |
|---|---|---|---|
| Binary classification, balanced | Accuracy or AUROC | F1, log-loss, ECE | Report ECE if probabilities matter (Ch. 4) |
| Binary classification, imbalanced (positive rare) | AUROC or AUPRC | F1, balanced accuracy, recall@fixed-precision | Accuracy is misleading; AUPRC dominates AUROC when positives are <5% (Ch. 4) |
| Multi-class classification | Balanced accuracy or macro-F1 | Per-class F1, confusion matrix, top-k acc | Macro > micro when small classes matter |
| Multi-label classification | Mean AP (mAP) or macro-F1 | Hamming loss, subset accuracy | Subset acc is brutal — use only if exact match required |
| Ordinal / ranking | NDCG, MRR, Kendall τ | Spearman ρ, MAP | Pick by where in the ranked list quality matters |
| Probabilistic / calibration-sensitive | Log-loss (cross-entropy) or Brier | ECE, reliability diagram | If decisions are threshold-based, calibrate then re-score (Ch. 4) |
| Regression, well-scaled | RMSE or MAE | R², MAPE | RMSE penalizes outliers; MAE if robust target needed |
| Regression, scale-free | SMAPE or MAPE | RMSE on log, R² | MAPE blows up near zero; prefer SMAPE for series with zeros |
| Time-series forecasting | MASE or sMAPE | RMSE, pinball loss for quantiles | MASE compares to seasonal naive; standard in M-competitions (Ch. 12) |
| Segmentation / detection | IoU / mIoU, mAP@IoU | Dice, panoptic quality | Set IoU threshold explicitly |
| NLP generation | Task-specific (BLEU/ROUGE/BERTScore) + human eval | Exact-match, perplexity | Auto-metrics are weak proxies; budget for human eval |
| RL / interactive | Cumulative reward over N environment steps | Sample efficiency, AUC of learning curve | Measure in env steps, NOT wall-clock — hardware bias (Ch. 12) |
| RL / agent-vs-agent | TrueSkill-style μ from matchmaking | Win rate vs reference agent | Pair submissions of similar μ to finish matches faster (Ch. 12) |
| AutoML / anytime learning | ALC (Area under Learning Curve) | Final score @ budget | Rewards systems that are good early AND late (Ch. 4, Ch. 12) |
| Adversarial attack | Attack success rate within ε-perturbation budget | Mean perturbation norm | Score = 1{‖x'-x‖≤ε ∧ f(x')≠y} (Ch. 12) |
| Adversarial defense | Robust accuracy under fixed attack suite | Clean accuracy gap | Always report clean accuracy too (Ch. 12) |
| Fairness-aware | Primary task metric + fairness constraint | Demographic parity, equal-opportunity gap, disparate impact | Constraint, not weighted sum, avoids gaming (Ch. 4) |
| Generative / synthetic data | Utility (downstream task score) AND privacy (membership-inference AUC) | FID, MMD | Single-axis scoring lets winners cheat one side (Ch. 12) |

- **Always report a confidence interval on the primary metric** — bootstrap (1000 resamples) is the default; if test set < 1k, results below 1% absolute differences are noise (Ch. 4).
- **For paired comparisons of models use a paired test (sign test, Wilcoxon, McNemar) — not unpaired t-test** — leverages that both models see the same examples (Ch. 4).
- **When ranking > 2 systems, use the Friedman test followed by Nemenyi post-hoc, or critical-difference diagrams** (Ch. 4).
- **Need ~1000+ test examples for stable rankings, ~10⁴ to separate strong models that are close** (Ch. 4). If you cannot afford that, switch to AutoML/code-submission protocols and run multiple seeds.

---

## 4. Baseline / starting kit

A starting kit is the single biggest determinant of participation quality.

- **Ship a runnable starting kit on day 1: data loader, dummy model, training loop, scorer, submission packager** — participants who cannot submit in their first hour mostly never submit (Ch. 5, Ch. 13).
- **Include at least two baselines: (a) a trivial one (constant predictor / random / nearest-neighbor) and (b) a competent one (off-the-shelf model on default hyperparameters)** — the trivial one bounds the metric, the competent one signals "is there room above me?" (Ch. 5).
- **Publish the score of each baseline on both public and private test sets at launch** — sets expectation, exposes leaderboard issues before they bite (Ch. 5).
- **A model that does not beat the trivial baseline must not appear on the final ranking** — filter at phase-transition time (Ch. 5, Ch. 11).
- **Make the starting kit work on a free Colab / single-GPU laptop** — gates participation; if you require 8×A100, prize must justify it (Ch. 13).
- **Ship a `scorer.py` that takes (truth, pred) and outputs the exact metric the leaderboard computes** — eliminates "I scored 0.91 locally and 0.42 on submit" tickets (Ch. 11).
- **Provide synthetic data sample even for code-submission/blind-data competitions** — participants cannot debug without something to print (Ch. 12).
- **For code-submission competitions, pin the docker image and Python/CUDA versions** — silent dependency drift breaks reproducibility (Ch. 11).
- **Document compute envelope: max RAM, max wall time per submission, GPUs available** — undocumented limits are perceived as cheating by organizers (Ch. 11, Ch. 13).

---

## 5. Phases

Two-phase structure (feedback + final) is the standard and the one that empirically works.

- **Always run at least two phases: development (with public leaderboard) and final (private test, limited or 1-shot submissions)** — single-phase competitions overfit the leaderboard (Ch. 5, Ch. 11).
- **Public leaderboard during dev phase shows score on a held-out *public* test set, NOT the training data and NOT the final test** (Ch. 5). Three disjoint sets: train, public test, private test.
- **Roelofs et al. (2019) found public/private overfitting is empirically uncommon on Kaggle** — but only because Kaggle enforces submission limits and uses a private test. The pattern, not optimism, is what protects you (Ch. 5).
- **Cap dev-phase submissions: 5–10/day is typical; daily caps deter brute-force overfitting more than total caps** (Ch. 5).
- **Final phase: 1–3 submissions total per team, scored on the never-seen private set** — anything more re-opens the overfit loophole (Ch. 5).
- **Minimum 40 days for the dev phase** — below that, only people who were already working on the problem can participate (Ch. 13).
- **Filter into the final phase: only teams who beat the competent baseline on dev** — keeps the final leaderboard meaningful and reduces grading load (Ch. 5, Ch. 11).
- **Consider the "Ladder" mechanism (Blum & Hardt 2015) for very long competitions** — releases a leaderboard score only when it significantly exceeds the previous one, formally bounding overfit (Ch. 5).
- **Force code/model submission for the final phase, even if dev was result-submission** — lets you re-run, audit, and verify the winner (Ch. 5, Ch. 11).
- **Re-score the entire dev leaderboard on the final private set, not only "selected" submissions** — selection by the participant is itself overfitting (Ch. 5).
- **Publish phase boundaries (dates, cutoffs) in UTC and freeze them** — last-minute extensions damage trust and create timezone-based unfairness (Ch. 13).

---

## 6. Anti-cheating

Treat the leaderboard as adversarial. Assume your most resourced participant is also your most resourceful cheater.

- **Code submission (γ-protocol) is the single strongest anti-cheat measure** — eliminates label memorization, label-flipping attacks against scorers, and most leakage exploits (Ch. 11, Ch. 12).
- **Sequester the private test labels on a machine that is not accessible to the leaderboard server** — leaderboard server stores hashes/IDs only (Ch. 11).
- **Detect probing attacks: monitor entropy/variance of submitted predictions over time and rate-limit any account whose submissions appear to binary-search the labels** (Ch. 5, Ch. 11).
- **Block multiple accounts: require verified email, IP/device fingerprinting, and a forum-based registration** — prize-eligible competitions are *legally* games of skill, not chance, which justifies KYC (Ch. 13).
- **Forbid external data unless explicitly whitelisted; if you allow it, require disclosure in the writeup** — undeclared external data is the most common post-hoc DQ reason (Ch. 5).
- **Require winners to submit reproducible code AND model weights AND a 1–2 page method writeup before prize award** — non-reproducible winners are disqualified; this is industry standard since DREAM (Ch. 5, Ch. 13).
- **For confidential / blind-data competitions, log every container's output size and inspect for steganographic exfiltration** — adversaries embed test labels in model weights (Ch. 12).
- **Hash and time-stamp the test set release; sign the scorer binary** — defends against later disputes about "the metric changed mid-competition" (Ch. 11).
- **Publish the rule set, prize eligibility, and DQ criteria before the first submission** — retroactive rules are unenforceable in practice (Ch. 13).
- **Always re-execute the top-N winning submissions on a clean machine before announcing the final ranking** — catches results that worked once on the participant's laptop (Ch. 5, Ch. 11).

---

## 7. Leaderboard hygiene

Ranking many submissions is a social-choice problem. Picking the wrong aggregation produces the wrong winner.

- **Display error bars (bootstrap CIs) for each leaderboard entry** — a leaderboard without uncertainty lets users believe rank 1 beat rank 2 even when the gap is noise (Ch. 4).
- **State the tie-breaking rule before launch** — common choices: (a) earlier submission wins, (b) lower runtime wins, (c) higher score on secondary metric wins. Pick one and publish (Ch. 4, Ch. 11).
- **For multi-metric leaderboards prefer average rank (Borda count) across metrics** — empirically robust, simple to explain, recommended in the book (Ch. 4). Mean of raw metrics is sensitive to metric scale.
- **Know the available ranking functions and when each fails: random dictator (one task), mean (scale-sensitive), median (robust but coarse), Borda/average rank (book default), Copeland (pairwise majority), Kemeny-Young (NP-hard, optimal but expensive)** (Ch. 4).
- **Gibbard's theorem: no deterministic ranking rule is simultaneously non-dictatorial, non-manipulable, and onto** — accept that some unfairness is mathematically unavoidable and pick a rule whose failure mode you can live with (Ch. 4).
- **Multi-objective: prefer constraint + primary objective ("highest accuracy s.t. fairness gap < 0.05") over weighted sum** — weighted sums let participants game the weights (Ch. 4).
- **Test statistical significance between top entries on the private set; if rank-1 and rank-2 are not significantly different at p<0.05, declare a tie or use a tie-breaker** (Ch. 4).
- **Run multiple seeds / multiple test splits when the test set is small (<1000)** — single-split rankings of close models are noise (Ch. 4).
- **Re-rank only AFTER filtering submissions that fail the trivial-baseline floor** — sub-baseline submissions can pollute Borda ranks (Ch. 5).
- **Refresh leaderboard at a fixed cadence; do not run on every submission** — bursty refresh discourages probing attacks (Ch. 11).
- **Anytime-learning competitions: rank by the area under the learning curve (ALC), not the final point** — rewards both fast and final performance (Ch. 4, Ch. 12).

---

## 8. Common pitfalls + smell-test checklist

Run this checklist; if any answer is "no" / "I don't know", do not launch.

- [ ] Can a competent ML practitioner submit something in < 1 hour from the starting kit? (Ch. 5, Ch. 13)
- [ ] Is the private test set sequestered on a machine the participants cannot reach? (Ch. 5, Ch. 11)
- [ ] Did you train a model on each candidate "leaky" feature alone and confirm it doesn't beat the trivial baseline? (Ch. 3)
- [ ] Does the train/public-test/private-test split match the unit of generalization (patient, time, document)? (Ch. 3, Ch. 4)
- [ ] Did at least one external reviewer try to solve the task before announce? (Ch. 2)
- [ ] Is the primary metric a single, named, well-documented function with a `scorer.py`? (Ch. 4)
- [ ] Do you have confidence intervals on the baseline scores? (Ch. 4)
- [ ] Is the dev phase ≥ 40 days? (Ch. 13)
- [ ] Is the final phase code-submission with ≤ 3 submissions per team? (Ch. 5)
- [ ] Are submission limits in place during dev (5–10/day)? (Ch. 5)
- [ ] Will sub-baseline submissions be filtered out before final ranking? (Ch. 5, Ch. 11)
- [ ] Did you publish a datasheet/data nutrition label? (Ch. 3)
- [ ] Did you check class balance and pick a balance-aware metric if needed? (Ch. 4)
- [ ] Is the dataset license, persistent URL, and post-competition home decided? (Ch. 3, Ch. 13)
- [ ] Are tie-breaking and DQ rules published before submission #1? (Ch. 4, Ch. 13)
- [ ] If prizes > 0, did legal confirm "game of skill" jurisdiction rules? (Ch. 13)

Pitfalls observed across the book's case studies:

- **"Voodoo splitting"**: random splits on grouped data → top-of-leaderboard solutions don't generalize (Ch. 3).
- **Metric mismatch**: optimizing AUC when downstream cost is asymmetric → winning model is useless in deployment (Ch. 2, Ch. 4).
- **Public-only leaderboard**: overfitting the public set in week 2 (Ch. 5).
- **Free-text submission of predictions**: probing attacks rebuild the labels (Ch. 11).
- **Ambiguous winner**: rank-1 and rank-2 within noise; no tie-breaker; PR disaster (Ch. 4).
- **Dead dataset**: 12 months post-competition the data is offline, the benchmark dies (Ch. 13).
- **Sub-1-hour SOTA**: someone gets near-SOTA on day 1 → task too easy, no scientific signal (Ch. 2).
- **Unlimited submissions + tiny test set**: ladder climbing by luck (Ch. 5).
- **External data ambiguity**: half of teams use ImageNet pre-training, half don't; results incomparable (Ch. 5).
- **Single-seed ranking on small test**: post-hoc reshuffles with bootstrap (Ch. 4).

---

## 9. Post-competition

The competition does not end at the prize ceremony — that is when its scientific value begins.

- **Plan for a 1-year post-challenge phase** with the leaderboard frozen but the dataset/code still hosted — recurrent benchmarks (CASP, ImageNet) become the de-facto evaluation only because they persist (Ch. 5, Ch. 13).
- **Require winners to (a) open-source code under a permissive license, (b) submit a method paper or report, (c) reproduce results on organizer compute** — before prize disbursement (Ch. 5, Ch. 13).
- **Run a post-mortem analysis: per-class breakdown, error analysis on top-K solutions, ablation of common techniques across leaders** — this is the publishable artifact, not the winner's name (Ch. 5).
- **Re-rank using multiple ranking functions (mean, Borda, Copeland) and report sensitivity** — strengthens the science and pre-empts disputes (Ch. 4).
- **Release ALL submissions (with consent) as a meta-dataset** — enables meta-learning research (Ch. 12); the AutoML series did this to great effect.
- **Maintain the leaderboard in "evergreen mode" with the same private test for at least 12 months** — late entrants and follow-up papers need a stable reference (Ch. 13).
- **Publish a NeurIPS/JMLR-track "lessons learned" paper, not just a results blog** — gives the dataset citation gravity and durability (Ch. 13).
- **Archive the docker image of the scoring stack** — without it, the leaderboard cannot be reproduced in 5 years (Ch. 11).
- **Survey participants on (a) time spent, (b) compute used, (c) friction points** — single highest-leverage input for designing the next iteration (Ch. 13).
- **Decide reuse policy: can future papers compare against your leaderboard? Under what license?** — explicit reuse rights are what turns a competition into a benchmark (Ch. 3, Ch. 13).

---

# adapt_to_context:

ai_text_detection:
  task_type: binary_classification_imbalanced
  primary_metric: AUROC
  secondary_metrics: [AUPRC, F1, recall_at_FPR_0.01, calibration_ECE]
  notes: >
    Adversarial domain. Treat as security task. Sequester held-out generators
    (model families NOT seen in train) for the private test, otherwise rankings
    measure overfit to known generators rather than detection. Two-phase mandatory:
    dev on known generators, final on unseen-generator + paraphrased + adversarially
    perturbed text. Code submission only. Forbid external "AI text" datasets
    unless declared.
  pitfalls:
    - Training and testing on outputs of the same LLM checkpoint inflates AUROC.
    - Length artifact: detectors learn "long text => AI"; control length distribution.
    - Tokenizer leakage: stylometric features tied to a single model's tokenization.

image_classification:
  task_type: multiclass_classification
  primary_metric: balanced_accuracy
  secondary_metrics: [top_5_accuracy, macro_F1, per_class_F1, confusion_matrix]
  notes: >
    Test set size 100/E rule applies: for 5% target error you need ~2k images
    per class minimum. Split by source/photographer/location to prevent
    "same-camera" leakage. Require code submission for final; ban ImageNet/CLIP
    pretraining unless explicitly allowed (and then disclosed). Use ALC if
    compute budget is constrained.
  pitfalls:
    - Random splits when images of the same object appear in train and test.
    - Class imbalance silently rewarded by accuracy.
    - Allowing test-time augmentation without limit -> rankings reflect TTA budget.

nlp_generation:
  task_type: text_generation
  primary_metric: human_evaluation_pairwise_winrate
  secondary_metrics: [BERTScore, BLEU_or_ROUGE_task_specific, exact_match_for_constrained_tasks, length_normalized_perplexity]
  notes: >
    Auto-metrics correlate weakly with quality. Budget human eval from day 1
    (cost dominates). Two-phase: dev with auto-metrics on public set, final
    with human eval on private prompts. Code submission, fixed decoding params
    OR participants declare decoding budget. Watch for prompt leakage in
    pretraining corpora.
  pitfalls:
    - BLEU/ROUGE alone -> reward-hacked outputs that copy reference n-grams.
    - Length-biased scoring -> verbose answers win.
    - Eval-set memorization by frontier models.

tabular_regression:
  task_type: regression
  primary_metric: RMSE_or_MAE
  secondary_metrics: [R2, MAPE_or_SMAPE_if_scale_free, calibrated_quantile_loss_for_intervals]
  notes: >
    Split by entity (customer/store/sensor) not row. Time-component? Split
    chronologically and forbid future leakage. Tabular winners are usually
    GBDT + careful CV; baseline should be exactly that to keep the bar high.
    Use Guyon formula to size test set for distinguishing close models.
  pitfalls:
    - Target encoding computed on full data leaks the target.
    - Random row split when rows from the same entity recur over time.
    - MAPE near zero -> use SMAPE or log-RMSE.

reinforcement_learning:
  task_type: rl_interactive
  primary_metric: cumulative_reward_over_N_env_steps
  secondary_metrics: [sample_efficiency_AUC, generalization_to_held_out_envs, win_rate_vs_reference_agent_if_self_play]
  notes: >
    Measure compute in environment steps, NOT wall-clock (hardware bias).
    Dev/test split = train envs vs held-out env seeds/levels (procedural
    generation strongly preferred; see ProcGen, MineRL). For agent-vs-agent
    use TrueSkill-style matchmaking and pair similar mu. Always evaluate on
    seeds NOT used in dev. Code submission mandatory.
  pitfalls:
    - Wall-clock scoring rewards owners of better hardware.
    - Reward hacking via simulator exploits -> reuse simulator across phases without freezing version.
    - Single seed evaluation -> rankings unstable.
