# Validation checks

`autocodabench validate` tests a Codabench competition bundle the way software
is tested: against an executable checklist, before it ever reaches a
participant. This page is the catalogue of those checks — what each one looks
at, and how it decides.

## 1. Structural

Does the bundle parse, upload, and hang together internally? These are the
hardest gates: if the structure is broken, nothing downstream can run.

| Check | Require LLM? | What it verifies | How it runs |
| --- | --- | --- | --- |
| Bundle schema and file references | No (code-based) | `competition.yaml` parses, every referenced file exists, programs carry runnable metadata, and every leaderboard key is actually written by the scoring program. *(Codabench schema, Yaml-Structure.md)* | Parses `competition.yaml` and checks each referenced file, program metadata, and leaderboard key against the schema. |
| Leaderboard is well-formed | No (code-based) | Each leaderboard declares a key, and its columns have unique keys and indices — collisions silently drop or overwrite a ranked column on the platform. *(Pavão Ch. 11; Yaml-Structure.md)* | Checks each leaderboard declares a key and that its column keys and indices are unique. |

## 2. Executable

Does the bundle actually run, and reproduce the scores it claims? These checks
execute code inside the competition's declared Docker image — the same image,
mounted the same way, that the Codabench worker uses.

| Check | Require LLM? | What it verifies | How it runs |
| --- | --- | --- | --- |
| Baseline runs end-to-end through scoring | No (code-based) | The bundle's own baseline passes through the full ingestion + scoring pipeline and produces a score. *(Pavão Ch. 5, 11)* | Runs the baseline through ingestion and scoring in the declared Docker image and checks a score is produced. |
| Baseline solutions shipped (trivial + competent) | No (code-based) | Two baselines exist and are declared: a trivial one bounds the metric, a competent one shows whether there is headroom above it. *(Pavão Ch. 5)* | Counts solution folders and checks they are declared in `competition.yaml`. |
| Starting-kit notebook executes cleanly | No (code-based) | The starting-kit notebook runs end-to-end inside the bundle's image, so participants can reproduce the evaluation locally. *(Pavão Ch. 5, 13)* | Executes the starting-kit notebook end-to-end in the declared Docker image. |
| Worker Docker image pinned | No (code-based) | `docker_image` carries an explicit, non-floating version tag — silent dependency drift breaks reproducibility. *(Pavão Ch. 11)* | Reads `docker_image` and checks it carries an explicit (non-`:latest`) version tag. |

## 3. Methodological

Is the competition *designed* to produce a trustworthy ranking? Phase
structure, submission economy, and test-set sizing are what stop the public
leaderboard from being overfit.

| Check | Require LLM? | What it verifies | How it runs |
| --- | --- | --- | --- |
| Development + final phase structure | No (code-based) | At least two phases exist — without a final phase on a private test set, the public leaderboard *is* the final ranking and overfits. *(Pavão Ch. 5, 11)* | Counts the phases declared in `competition.yaml`. |
| Development phase ≥ 40 days | No (code-based) | The development phase runs long enough to reach people who were not already working on the problem. *(Pavão Ch. 13)* | Parses the first phase's start/end and computes the span in days. |
| Daily submission cap on development phases | No (code-based) | Development phases cap submissions per day (typically 5–10) so the leaderboard cannot be brute-forced. *(Pavão Ch. 5)* | Reads `max_submissions_per_day` on each development phase. |
| Final phase total-submission limit ≤ 3 | No (code-based) | The final phase allows only 1–3 submissions on the never-seen private set, closing the overfit loophole. *(Pavão Ch. 5)* | Reads `max_submissions` on the final phase. |
| Final phase closes (review window exists) | No (code-based) | The final phase declares an end date — an open-ended phase never closes, leaving no defined point for organizer review. *(Pavão Ch. 5)* | Checks the final phase declares an end date. |
| Phase dates are coherent and ordered | No (code-based) | Every phase ends after it starts, and phases run in chronological order — scrambled dates silently break the timeline on the platform. *(Pavão Ch. 11; Yaml-Structure.md)* | Parses every phase's start/end; checks each end ≥ start and starts are non-decreasing. |
| Test set sized for the anticipated error rate | No (code-based) | The test set holds ≥ 100/E examples for anticipated error rate E, so score differences near the top are signal, not noise. *(Pavão Ch. 4)* | Compares the reference/test row count against 100 / anticipated-error-rate *(needs the `anticipated_error_rate` fact)*. |
| Baseline range (trivial → SOTA) documented | Yes | The pages describe a *range* of baselines — a trivial bound and a competent/SOTA method — and a meaningful gap between them, evidence the task has headroom worth contesting. *(Pavão Ch. 5)* | An LLM checks the pages describe a baseline range and the performance gap. |
| Task difficulty is calibrated | Yes | The pages argue the task is challenging but solvable — neither trivial nor impossible — with the baseline-to-headroom gap as evidence. *(Pavão Ch. 5)* | An LLM checks the pages argue the task is challenging but solvable. |
| Metric choice is justified | Yes | The pages explain *why* the chosen metric measures success on this task, not merely name it. *(Pavão Ch. 4)* | An LLM checks the pages justify why the metric assesses the task. |
| Score-difference significance addressed | Yes | When the ranking leans on fine distinctions, the pages account for how meaningful score differences are — error bars, significance, or noise. *(Pavão Ch. 4)* | An LLM checks whether significance / error bars are addressed when the ranking needs it. |

## 4. Data & leakage

Are the splits clean, and is the ground truth genuinely hidden? Leakage is the
failure mode that quietly invalidates a whole competition.

| Check | Require LLM? | What it verifies | How it runs |
| --- | --- | --- | --- |
| Reference (ground-truth) data is not participant-visible | No (code-based) | No file in the hidden `reference_data` role appears byte-for-byte under a participant-visible role (`public_data` / `input_data`) — an identical file in a visible role is leaked labels. *(Pavão Ch. 11, 3)* | Hashes `reference_data` files and checks none appear under `public_data/` or `input_data/`. |
| Data quantity & availability justified | Yes | The pages justify that the dataset is large enough for conclusive results, will remain available after the contest, and keeps ground truth confidential. *(Pavão Ch. 3)* | An LLM checks the pages justify dataset size, post-contest availability, and GT confidentiality. |
| Dataset is not deprecated or recalled | Yes | The dataset has not been deprecated, recalled, or superseded by a corrected release — a due-diligence screen a bundle cannot answer for itself. *(Pavão Ch. 3)* | Surfaced for human confirmation *(needs the `dataset_name` fact)*; an LLM drafts the due-diligence note. |
| Per-feature leakage probe | Yes | Each candidate leaky feature was trained on alone and confirmed not to beat the trivial baseline — covering ground-truth-in-features, duplicate entities, and processing leakage. *(Pavão Ch. 3)* | Requires a training run; surfaced for human confirmation, with an LLM suggesting likely leak sources. |

## 5. Documentation

Could a newcomer read the pages and actually compete? Most onboarding failures
are documentation failures, so this is the largest dimension — and almost
entirely advisory.

| Check | Require LLM? | What it verifies | How it runs |
| --- | --- | --- | --- |
| Leaderboard columns declare sorting direction | No (code-based) | Every ranked column declares its direction — a missing `sorting` silently inverts metrics where lower is better. *(Pavão Ch. 4; Yaml-Structure.md)* | Checks each ranked leaderboard column declares a sorting direction. |
| Sort direction matches the metric's semantics | No (code-based) | The declared sort matches the named metric's known direction — catches the classic accuracy-sorted-ascending inversion that ranks the worst submission first. *(Pavão Ch. 4)* | Looks up the metric name's known direction and compares it to the column's sorting. |
| Submission mode (result vs code) declared | No (code-based) | Participants are told whether they submit a prediction file or runnable code; the mode is implied by the ingestion program and must also be stated in the pages. *(Pavão Ch. 2, 11)* | Detects result- vs code-submission from the ingestion program and checks the pages state it. |
| Runnable starting kit shipped | No (code-based) | A starting kit ships under `starting_kit/` — the single biggest participation lever, since people who cannot submit in their first hour mostly never do. *(Pavão Ch. 5, 13)* | Looks for files shipped under `starting_kit/`. |
| Challenge type declared (regular / hackathon / live) | No (code-based) | The cadence is declared — regular (months), hackathon (a day or two), or live/on-site — because it sets participant expectations and gates the on-site-readiness criteria. *(Pavão Ch. 2)* | Reads the `challenge_type` fact and checks it is one of regular / hackathon / live. |
| External-data rule declared and documented | No (code-based) | The pages state an external-data / pre-training policy — undeclared external data is the most common post-hoc disqualification fight. *(Pavão Ch. 5)* | Scans the pages for an external-data policy and cross-checks the declared fact. |
| Task is clearly framed with a single objective | Yes | The overview states a single, focused objective with motivation, not a bundle of loosely related goals. *(Pavão Ch. 2)* | An LLM judges whether the overview states a single, clear task. |
| Abstract covers the five standard elements | Yes | The overview opens with the five elements a proposal abstract should state: motivation + impact, task + data, novelty, baselines + results, and scientific questions. *(Pavão Ch. 2)* | An LLM checks the overview for the five standard abstract elements. |
| Background & impact are stated | Yes | The pages motivate the problem and state its impact, audience, and a real-world scenario — the "hook" without which a challenge reads as arbitrary. *(Pavão Ch. 2)* | An LLM checks the pages state impact, audience, and a real scenario. |
| Task tied to an application scenario | Yes | The pages connect the task to a concrete real-world scenario, or justify the abstraction. *(Pavão Ch. 2)* | An LLM checks the pages tie the task to a real scenario or justify the abstraction. |
| Novelty is positioned vs prior work | Yes | The pages state what is new relative to prior challenges/benchmarks, or that it is a new edition reusing/extending earlier data. *(Pavão Ch. 2)* | An LLM checks the pages state how the challenge differs from prior work. |
| Challenge protocol is described | Yes | The pages describe what participants do, what they submit, the evaluation procedure, the phase structure, and the leaderboard — the participant-facing contract. *(Pavão Ch. 2)* | An LLM checks the pages describe the protocol end-to-end. |
| Submission format + interface are documented | Yes | The pages and starting kit give enough that a participant could produce a valid first submission. *(Pavão Ch. 2, 11)* | An LLM checks the pages + starting kit document the submission format and interface. |
| Data is documented adequately | Yes | The data page documents size, splits, visibility, and data policy — enough to understand the dataset. *(Pavão Ch. 3, 5)* | An LLM checks the data page documents size, splits, visibility, and policy. |
| Metric and ranking are explained | Yes | The evaluation page explains the metric and how the ranking is decided, well enough to act on. *(Pavão Ch. 4)* | An LLM checks the evaluation page explains the metric and ranking. |
| Starting kit mirrors evaluation conditions | Yes | The kit lets participants develop and test under conditions identical to the evaluation platform, so submissions are not blind. *(Pavão Ch. 5)* | An LLM checks the pages + kit allow testing under the same conditions as evaluation. |
| Rules cover the launch-critical clauses | Yes | The rules/terms cover the clauses a launch depends on. *(Pavão Ch. 13, 4, 2)* | An LLM reads the rules/terms pages for missing launch-critical clauses. |
| Schedule leaves adequate time | Yes | The schedule allows preparation, a development window (~90 days is the norm), and post-close review, and states what is already ready. *(Pavão Ch. 5)* | An LLM checks the pages present a schedule with adequate development and review time. |
| Pages ↔ `competition.yaml` consistency | Yes | The participant-facing pages do not contradict the machine configuration. *(Pavão Ch. 11, 13)* | An LLM compares the pages against `competition.yaml` and reports contradictions. |
| Tutorial / documentation material referenced | Yes | The pages reference onboarding material — a white paper, FAQ, notebooks, or video — beyond the bare task description. *(Pavão Ch. 2)* | An LLM checks the pages reference tutorial material. |
| Topical keywords / orientation present | Yes | A reader gets quick topical orientation — keywords, tags, or a one-line topic sentence. Minor polish, flagged only when orientation is wholly absent. *(Pavão Ch. 2)* | An LLM checks the pages give quick topical orientation. |

## 6. Governance

Is the competition fair, legal, and reproducible beyond its launch? These are
the review, licensing, ethics, and persistence criteria — several of them
things only a human can attest.

| Check | Require LLM? | What it verifies | How it runs |
| --- | --- | --- | --- |
| Dataset licence declared | No (code-based) | The dataset carries an explicit licence, so participants know their usage rights and the data can outlive the leaderboard. *(Pavão Ch. 3, 13)* | Checks the `data_license` fact, else scans the pages + `competition.yaml` for a licence token. |
| Prize structure described when prizes are offered | No (code-based) | If prizes are offered, the pages describe what is awarded and to whom, so the stakes are clear and disputes have a documented basis. *(Pavão Ch. 13)* | When `prizes=true`, scans the pages for prize/award/winner prose. |
| Account & anonymity policy stated | Yes | The rules state the single-vs-multiple-account and anonymity policy — a common source of disqualification disputes. *(Pavão Ch. 13)* | An LLM checks the rules state the account and anonymity policy. |
| Cheating prevention is addressed | Yes | The rules address cheating detection and prevention, so disputes over multi-accounting, label leakage, or collusion have a documented basis. *(Pavão Ch. 2, 5)* | An LLM checks the rules/pages address cheating detection and prevention. |
| Rules stability / amendment policy stated | Yes | The rules state they are fixed for the duration, with an amendment policy if changes become necessary — serious competitors need stable winning conditions. *(Pavão Ch. 13)* | An LLM checks the rules state they are fixed, with an amendment policy. |
| Human-judging protocol specified | Yes | When judging is human/subjective, the criteria are specific and orthogonal, the tie-break is defined, and the judges' qualifications are given. Applies when `human_judging=true`. *(Pavão Ch. 4)* | When `human_judging` is declared, an LLM checks the criteria, tie-break, and judge qualifications. |
| Equitable resource access addressed | Yes | When entering needs special hardware or heavy compute, the pages address equitable access for under-resourced participants. Applies when `special_hardware=true`. *(Pavão Ch. 5)* | When special hardware is declared, an LLM checks the pages address equitable access. |
| Dataset licence and post-competition home | Yes | The dataset has an explicit licence, a persistent identifier or URL, and a decided post-competition home — a benchmark whose data dies at leaderboard close is not a benchmark. *(Pavão Ch. 3, 13)* | Surfaced for human confirmation; an LLM can flag missing licence/DOI cues. |
| Datasheet / data nutrition label | Yes | A datasheet (Gebru et al.) covering provenance, consent, known biases, and intended use is published with the dataset. *(Pavão Ch. 3)* | Surfaced for human confirmation; an LLM can draft a datasheet checklist. |
| PII minimised and consent / ethics approval obtained | Yes | PII is minimised and, where real human-subject data is used, informed consent (and ethics approval where applicable) has been obtained. *(Pavão Ch. 3)* | Surfaced for human confirmation; an LLM can flag PII risk. |
| External proposal review | Yes | At least one external reviewer (ideally 3+) attempted the task before announcement — a pillar of successful challenges and the cheapest dead-end-task catch available. *(Pavão Ch. 2)* | Surfaced for human confirmation; an LLM can suggest what to verify. |
| Organizing team covers the required roles | Yes | The team covers coordinators, data providers, platform administrators, baseline-method providers, beta testers, and evaluators — with relevant competence and a diversity note. *(Pavão Ch. 2)* | Surfaced for human confirmation; an LLM can list the roles to staff. |
| Promotion plan, including outreach | Yes | A plan exists to promote participation and to attract participants from groups under-represented in challenge programs. *(Pavão Ch. 2)* | Surfaced for human confirmation; an LLM can draft a promotion checklist. |
| Prize legality (game of skill) | Yes | Where prizes are offered, their legality (e.g. game-of-skill status) has been confirmed. Auto-passes when `prizes=false`. *(Pavão Ch. 13)* | Surfaced for legal confirmation when prizes are declared; LLM-assisted. |
| Live-challenge on-site logistics arranged | Yes | For a live/on-site challenge, the on-site logistics are arranged. Auto-passes unless `challenge_type=live`. *(Pavão Ch. 2)* | Surfaced for human confirmation when the challenge is live; LLM-assisted. |

## Sources

Codabench bundle schema documentation · Pavão et al. (2024), *AI Competitions
and Benchmarks: The Science Behind the Contests*.
