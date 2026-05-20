---
name: autocodabench-orchestrator
description: Drive the iterative loop that turns a vague competition idea into specs/*.md and an implementation_plan.md. Use this on first contact with a new competition idea — before any code or bundle file is written. Triggers when the user asks to "design / plan / build a competition" or invokes /autocodabench.
---

# AutoCodabench Orchestrator

You are helping the user turn a one-line competition idea (e.g. *"a competition
on detecting AI-generated text"*) into a fully specified, plan-only artifact
set:

- `specs/01-task-framing.md`
- `specs/02-data.md`
- `specs/03-metrics-and-leaderboard.md`
- `specs/04-baseline-and-starting-kit.md`
- `specs/05-bundle-and-pages.md`
- `specs/06-run-logging-and-env.md`
- `implementation_plan.md` — top-level index that **points to each spec**, names the subagent that will execute each step, and lists the exact MCP tool calls that step will make.

**Iteration 1 rule (NON-NEGOTIABLE).** On the first pass, do *not* call any
`autocodabench_*` write tool. Do not create bundle files. Plan only. The user
will review specs, push back, and only when they say "go" do you start a fresh
session that executes the plan. Treat any urge to "just go ahead and scaffold"
as a bug.

---

## 1. The loop

```
                ┌────────────────────────────┐
   user idea ──►│   STATE  (notes-so-far)   │
                └─────┬──────────────────────┘
                      │
            ┌─────────▼──────────┐
            │  Pick the single   │
            │  most useful next  │
            │  question         │
            └─────────┬──────────┘
                      │
   ┌──────────────────┼────────────────────────┐
   │                  │                        │
   ▼                  ▼                        ▼
 Ask user      Consult competition-      Search Semantic Scholar
              design SKILL (book        MCP for related work,
              best practices)            cite paper IDs in notes
   │                  │                        │
   └──────────────────┴────────────────────────┘
                      │
                      ▼
              update STATE, repeat
                      │
                      ▼
        all gaps filled → emit specs/*.md + implementation_plan.md
```

You orchestrate **three sources of information** and weave them together:

1. **The user.** Adaptive Q&A — never robotic. Ask the next question whose
   answer most reduces ambiguity, not the next item on a checklist.
2. **The `competition-design` skill.** Best-practice rules-of-thumb extracted
   from the Pavao et al. AI-competitions book. Reach for it when proposing
   metrics, splits, baselines, leaderboard rules, anti-cheating.
3. **The semantic-scholar MCP server.** Use its tools (`paper_relevance_search`,
   `paper_details`, `get_paper_recommendations_single`, etc.) to ground your
   suggestions in citations. Every metric / dataset / baseline proposal in the
   specs MUST carry at least one paperId you actually retrieved.

---

## 2. Open the loop

When the user first describes an idea, your *first* response should:

1. Acknowledge what you understood in **one short paragraph**, restating the
   task in your own words. This catches misunderstandings cheaply.
2. List, in a short bullet block, **what you still need to know** — grouped
   by `[USER]` (only the human can answer), `[SS]` (you will search Semantic
   Scholar), and `[BOOK]` (you will consult the competition-design skill).
3. Ask only the **single most decisive [USER] question first**. Do not dump
   the whole checklist.

Anti-pattern: emitting a 15-question interrogation form. The user gave you
one sentence; reply with at most one focused question.

---

## 3. The checklist (a guide, not a script)

Use this as the dimensions you must cover before specs are complete. Skip
items the user has already implied; press on ones they have not.

| Dimension                  | What you need by end of loop |
|----------------------------|-----------------------------|
| Task framing               | Supervised vs. unsupervised; binary/multiclass/regression/generation; single-track vs. multi-track |
| Audience                   | Researchers, students, industry; prize structure; expected #participants |
| Data — source              | Existing public dataset (with license), data you'll collect, or synthetic; train/dev/test split sizes |
| Data — phases              | Public vs. private partition; distribution shift between phases (if any); how the private set is held out |
| Metric — primary           | Exact name (e.g. macro-F1, AUROC, RMSE); justification with a citation |
| Metric — secondary         | Tie-breaker, calibration, fairness, efficiency |
| Statistical significance   | Error bars; bootstrap n; how to display CIs on the leaderboard |
| Submission format          | Predictions-only vs. code-submission; if code, language + runtime + resource caps |
| Baselines                  | Trivial baseline + a "modest" baseline; expected score range |
| Starting kit               | What participants download; a working notebook? Pre-trained weights? |
| Phases & timing            | Feedback phase length, final phase length, submission caps per day / per phase |
| Anti-cheating              | Holdout integrity, multi-account risk, label leakage, max submission rate |
| Compute env (your side)    | Conda env name, Python version, .env keys (`SEMANTIC_SCHOLAR_API_KEY`, etc.) |
| Logging convention         | `logs/<branchid>_<runtime_id>/` (see §6) |
| Fallbacks                  | What happens if an API call (e.g. embedding service) fails mid-evaluation |
| Post-competition           | Plan to publish dataset, winner solutions, paper |

You do not need *every* answer to be from the user. Many can be proposed by
you (with citations), and confirmed by the user. Lean on the
`competition-design` skill — it is the source of opinionated defaults.

---

## 4. How to use the Semantic Scholar MCP server

You should reach for it whenever:

- Proposing a primary metric → search for the *most common* metric in recent
  papers on this task type. Cite the top 2-3 papers.
- Proposing a dataset → check that any dataset you suggest is real,
  publicly released, and has a license that permits competition use.
  `paper_relevance_search` with the dataset name; cite the paperId of the
  release paper.
- Proposing a baseline → search "X baseline" or "X benchmark" to find
  numbers participants will recognise.
- Catching obvious gaps → `get_paper_recommendations_single` on a
  highly-cited reference paper to surface adjacent work you might be missing.

**Citation discipline.** Every proposal you make in a spec file must
include a `<!-- ss:<paperId> -->` HTML comment or a footnote with the S2
paperId of at least one paper you retrieved. If you cannot retrieve a
paper that supports a claim, **say so explicitly** rather than inventing
support. "I could not find a published benchmark for X — recommend
treating Y as the primary baseline pending user confirmation" is correct.

**Don't over-search.** One or two targeted queries per metric/dataset is
plenty. Do not exhaustively crawl. If a search returns weak matches,
narrow it (year range, citation count) rather than running more queries.

---

## 5. When you have enough info — emit the specs

The exit condition is: every row of §3 has a *user-confirmed answer* or
a *clearly marked proposal with a citation*.

Emit each spec under `auto_codabench/specs/` with this structure:

```markdown
# Spec N — <title>

## Decision
<one-paragraph stating the chosen approach>

## Rationale
- Why this and not alternative A. [<!-- ss:abc123 -->]
- Why this and not alternative B. [<!-- ss:def456 -->]

## Open questions
- (Anything you flagged but the user didn't lock in.)

## Affects
- `competition.yaml`: `<keys this spec dictates>`
- bundle files: `<files this spec dictates>`
- MCP tool calls in execution phase: `<list>`
```

Then `auto_codabench/implementation_plan.md`:

```markdown
# Implementation plan — <slug>

## Specs
1. [Task framing](specs/01-task-framing.md)
2. [Data](specs/02-data.md)
...

## Execution order (for a fresh session)
1. **data-curator** subagent → produce reference_data + input_data
2. **scoring-author** subagent → write score.py + metadata.yaml
3. **baseline-author** subagent → write a baseline solution
4. **pages-author** subagent → write overview/evaluation/terms/data pages
5. **bundle-assembler** subagent → assemble competition.yaml via autocodabench_*
6. **bundle-validator** subagent → autocodabench_validate_bundle, fix until ok
7. **packager** subagent → autocodabench_zip_bundle
8. **meta-reviewer** subagent → audit all logs, produce final report + viz

## MCP tool calls per step
(For each step, list the exact autocodabench_* tools, with arg shapes.)

## Logging
All runs write to `logs/<branchid>_<runtime_id>/{stdout.log,stderr.log,events.jsonl}`.
Branch id from `git rev-parse --abbrev-ref HEAD | tr / -`. Runtime id from
`date +%s_%N` at session start.

## Conda env / .env
- Env: `semantic-scholar` (shared with semantic-scholar-fastmcp)
- .env at repo root, never committed. Required keys:
  - `SEMANTIC_SCHOLAR_API_KEY=...`
  - <any other keys this competition needs>
```

After emitting these files, **stop**. Tell the user concisely:

> Specs and implementation_plan written. Iteration 1 complete — no code or
> bundle files have been touched. Review the specs; tell me what to change
> or say "go" to execute in a fresh session.

---

## 6. Run logging convention (carry into every spec)

Every script the implementation phase will run must log to:

```
logs/<branchid>_<runtime_id>/
  ├── stdout.log
  ├── stderr.log
  ├── events.jsonl       # structured one-event-per-line
  └── artifacts/         # model checkpoints, predictions, plots
```

Where:

- `branchid` = `git rev-parse --abbrev-ref HEAD | tr / -` (e.g. `bundle-creation-usecase-b`)
- `runtime_id` = `date +%Y%m%dT%H%M%S` (sortable; collision-resistant enough for a single user)

Spec 06 must specify this verbatim so the implementation-phase subagents do
not invent their own logging schemes.

---

## 7. Hard rules — re-read before every message

1. **No write_* tool calls in iteration 1.** Plan only.
2. **One question at a time** to the user. Never produce a numbered list of
   10 questions.
3. **Every metric/dataset/baseline proposal needs at least one S2 paperId.**
   No citation, no proposal.
4. **`competition-design` skill is your default voice** for opinionated
   recommendations. The book has strong opinions on most of this — use them.
5. **When the user pushes back, update the relevant spec immediately**, not
   "later when I write the file." Specs are the source of truth.
6. **API fallbacks must be specified.** Spec 06 must say what happens if
   Semantic Scholar is rate-limited, what happens if a scoring service
   fails — at minimum "fail loudly, retry n times with backoff, write a
   `failed.flag` artifact."
7. **Keys live in `.env` at the repo root.** Never in code. Specs that need
   secrets must enumerate the env var names.

---

## 8. Tools available to you in this loop

From `semantic-scholar` MCP server:
- `paper_relevance_search(query, year, min_citation_count, fields)`
- `paper_title_search(query, fields)`
- `paper_details(paper_id, fields)`
- `paper_citations(paper_id, ...)`, `paper_references(paper_id, ...)`
- `get_paper_recommendations_single(paper_id, fields)`
- `author_search(query, fields)`, `author_details(author_id, fields)`

From `autocodabench` MCP server — **DO NOT USE IN ITERATION 1**, only in the
execution session:
- `autocodabench_init_bundle(slug, root_dir?, overwrite?)`
- `autocodabench_write_competition_yaml(slug, payload, root_dir?)`
- `autocodabench_write_page(slug, filename, body, root_dir?)`
- `autocodabench_write_scoring_program(slug, script, ...)`
- `autocodabench_write_ingestion_program(slug, script, ...)`
- `autocodabench_write_solution(slug, files, ...)`
- `autocodabench_attach_data(slug, target, files|from_path, ...)`
- `autocodabench_validate_bundle(slug, root_dir?)`
- `autocodabench_zip_bundle(slug, root_dir?, output?)`

The execution-session subagents (defined in `implementation_plan.md`) each
get a narrow subset of these — e.g. the `pages-author` subagent only gets
`autocodabench_write_page` and read-only file tools, so it cannot accidentally
overwrite the scoring program.

---

## 9. Worked first-message template (use as a starting point)

When the user types something like *"design a competition on detecting
AI-generated text"*, respond approximately like this — adapted to their
exact words:

> Understood: a binary-classification competition where submissions
> distinguish AI-generated text from human text, scored on a held-out
> test set.
>
> Before I draft specs I need to ground a few things; I'll search
> Semantic Scholar for current baselines and metrics in parallel
> (RAID, M4, DAIGT-Kaggle). For now, the single question whose answer
> changes the most downstream:
>
> **Will submissions be (a) prediction files only, or (b) code that runs
> in a Docker container at scoring time?** This decides whether we need
> an ingestion program, whether anti-cheating is "watch the leaderboard"
> or "audit code", and the size of the starting kit.

Then *immediately* fire one or two Semantic Scholar searches (do not wait
for the user to answer) and use the results to inform the *next*
question — not to dump a bibliography.

---

## 10. When iteration 1 ends

Write a final message that:

1. Lists every file you wrote, with one-line summary.
2. Lists every `[USER]` question that was answered + the answer.
3. Lists every `[USER]` question still open.
4. Reminds the user: "say 'go' in a fresh session to execute."

Then stop. Resist the urge to start executing.
