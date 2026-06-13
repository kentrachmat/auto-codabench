# autocodabench — evaluator's guide

This document is addressed to an external scientific reviewer who wishes to
evaluate the software with minimal setup. It is self-contained and offers
three complementary routes: a zero-install web demonstration (Part A), a
five-minute local evaluation that requires no accounts or API keys
(Part B), and a guided tour of the repository together with the engineering
and scientific standards against which it should be checked (Part C).

The scientific companion document — which states what the software claims,
how each claim is tested, and how to reproduce every test — is
[`scientific-validation.md`](./scientific-validation.md). A reviewer with
time for only one document should read that one.

---

## Part A — Web demonstration (browser only, approximately 10 minutes)

Prerequisites: the Space URL and the shared password, both provided by the
maintainer. Nothing is installed on the reviewer's machine, and sessions
are cost-capped server-side.

1. Open the Space URL and sign in with any username together with the
   shared password.
2. You begin in Phase 1 (Plan). Type a competition idea, for example:
   > *Design a competition on detecting AI-generated text.*
3. The agent drafts the seven design dimensions (task framing, data,
   metric, baselines, phases, rules, schedule) and asks one or two scoping
   questions. Answer them, or instruct the agent to use its defaults.
   Observe that the design proposals carry citations: the design rules
   derive from Pavão et al. (2024), *AI Competitions and Benchmarks*,
   rather than from model improvisation.
4. When the agent saves `implementation_plan.md`, the right-hand workspace
   panel shows the rendered plan. This document constitutes the entire
   interface between phases: Phase 2 starts with no memory of the chat,
   only this file. This is the auditability mechanism, not an incidental
   property of the user interface.
5. Click **Advance to Phase 2 — Competition Creation** in the phase bar.
   A fresh agent reads the plan and writes the bundle; the interface
   displays one chip per tool call (`init_bundle`,
   `write_scoring_program`, `write_competition_yaml`, and so on). Each
   chip expands to the exact input and output JSON of that call — the
   audit trail, rendered live.
6. When the agent finishes, the workspace footer offers the **competition
   bundle (.zip)** (the uploadable artifact) and **workspace.zip** (the
   full session record: plan, transcript, and every tool call).
7. (Optional) The **Publish to Codabench** form uploads the zip using
   credentials that you type; the upload goes directly to codabench.org
   and never passes through the model. This step may be skipped unless
   you wish to host a live competition under your own account.

The footer of each turn shows the running cost against the session cap.

**Scope of the web demonstration.** It demonstrates the plan-to-build
flow, phase isolation, and the per-action audit trail. The validator —
the scientific core of the software — is better exercised locally, as
described in Part B.

---

## Part B — Local evaluation, keyless (approximately 5 minutes)

Prerequisites: Python ≥ 3.10. No Anthropic account, no API key, and no
network access after installation.

```bash
git clone <repo-url> && cd auto-codabench
pip install -e '.[dev]'

# 1. The unit suite: 41 tests, < 1 s, keyless by policy.
python -m pytest tests/

# 2. Offline end-to-end: a recorded agent run is REPLAYED against the
#    real authoring layer — the bundle is genuinely rebuilt on your
#    machine, then validated and zipped. Deterministic; no model access.
autocodabench demo --out /tmp/demo

# 3. The validator on the result (also works on any hand-written bundle).
autocodabench validate-bundle /tmp/demo/demo-ai-text-detection.zip

# 4. The executable checklist: every check, its tier, its citation.
autocodabench checks list
```

The validation report comprises four sections, and reading them
deliberately is recommended, as they encode the project's epistemics:
**Gate failures** (computed by code, blocking), **Findings** (advisory
design risks, each with a citation), **Attestations required** (criteria
verifiable only by a human, which the tool declines to pretend to
verify), and **Skipped** (checks whose declared facts are missing —
reported explicitly rather than passed silently).

If Claude authentication is available (a Claude Pro/Max login via Claude
Code, or `ANTHROPIC_API_KEY`), two further probes are worthwhile:

```bash
autocodabench validate-bundle /tmp/demo/demo-ai-text-detection --judged
# → the LLM-judged tier. Then plant a contradiction and re-run:
#   edit pages/overview.md to claim "max 20 submissions/day"
#   (the config enforces 5) — the judge should flag exactly that line.

autocodabench create "Iris species classification from tabular \
    measurements, balanced accuracy, result submission" --verbose
# → the full live pipeline (~10–20 min, ~$2–4): plan → build →
#   self-validation (the bundle's own baseline + notebook must execute)
#   → validation report.
```

---

## Part C — Reading the repository

### Suggested order (approximately 1 hour)

| # | Read | What it answers |
|---|------|-----------------|
| 1 | `README.md` | What the tool is, and the friction it removes |
| 2 | [`docs/scientific-validation.md`](./scientific-validation.md) | The claims, every test type with its exact procedure and oracle, the designed experiments, and the threats to validity |
| 3 | [`docs/architecture.md`](./architecture.md) | Layering, the backend seam, design rationale, and invariants |
| 4 | `src/autocodabench/checks/` (start at `base.py`, then `deterministic.py`) | The check contract in code — to be compared against what §3.4 of the scientific document promises |
| 5 | `tests/` | What is actually asserted (note `tests/conftest.py`: the test fixture is identical to the replay demo) |
| 6 | `experiments/bundle_creation_test/README.md` | The ground-truth experiment design, including the data-leakage and blinding protocol |
| 7 | `src/autocodabench/skills/*/SKILL.md` and the sibling READMEs | The agents' behavioral contracts and their provenance |

### Engineering standards checklist (verify rather than trust)

| Standard | Where to verify |
|---|---|
| OSI license | `LICENSE` / `pyproject.toml` (MIT) |
| Installable package | `pip install -e .`; console script `autocodabench` (subcommands `validate-bundle`, `demo`, `create`, …) |
| Test suite, keyless, fast | `python -m pytest tests/` |
| CI on every push (3.10–3.13, Linux and macOS, including the offline end-to-end run and a wheel-content check) | `.github/workflows/ci.yml` |
| Versioning and changelog | `CHANGELOG.md`, `autocodabench --version` |
| Documentation beyond a README | `docs/` (user guide, architecture, this guide, scientific validation) |
| Reproducible runs | any run directory: `tool_calls/`, `events.jsonl`, `meta.json` (model and git SHA recorded) |
| Honest statement of limitations | `scientific-validation.md` §5; the attestation tier itself |

### Anticipated objections, and where each answer lives

**Objection: the software is merely a wrapper around a chatbot.**
Response: the contribution is the scaffolding, which is inspectable in
code — the typed tool surface to which the agent is confined
(`mcp/tools/`), phase isolation through a locked plan
(`agent/pipeline.py`), execution oracles independent of the agent
(`runner/execution.py`), the three-tier check registry (`checks/`), and
record/replay (`backends/replay.py`). The keyless demonstration exercises
the entire stack with the model removed.

**Objection: a non-deterministic generator cannot be tested.**
Response: see §2 and §3.3 of `scientific-validation.md` — artifact-level
oracles, repeated-run success rates, and a deterministic sub-model layer
proven by replay.

**Objection: the LLM grades its own homework.**
Response: it does not. Generation verdicts come from code (the linter,
sandbox exit codes, and parsed scores). The single LLM-judged check is
advisory by construction and degrades to SKIPPED when its output is
unparseable (`checks/judged.py`, approximately 30 lines of policy).

**Objection: it is unclear what evidence exists so far versus what is
merely planned.**
Response: status tags appear throughout `scientific-validation.md`:
implemented (with commands), piloted (N=1, artifacts retained), and
designed (the E1–E4 protocols).
