---
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

# autocodabench

autocodabench is a library for agentic authoring and pre-launch validation of
[Codabench](https://www.codabench.org) competition bundles.

Organizing a machine-learning competition on Codabench requires hand-writing
an interlocking set of YAML configurations, scoring programs, and data
splits; a single inconsistency ships silently and fails on live
participants. autocodabench addresses this problem in two ways. First, it
turns a one-line idea (or a proposal PDF) into a validated, uploadable
bundle. Second, it tests bundles — whether generated or hand-written — the
way software is tested: against an executable checklist before launch.

> **Note:** this `README.md` also serves as the Hugging Face Spaces metadata
> file. The YAML header above configures the Space (Docker SDK, port 7860).
> It must not be deleted on the HF side; the prose below it may be edited
> freely.

## Quickstart (no API keys required)

The following commands exercise the full pipeline without any LLM
credentials.

```bash
pip install -e .            # (PyPI release pending — install from a checkout)

# Watch the full pipeline offline: a recorded agent run is replayed against
# the real authoring layer, then validated and zipped.
autocodabench demo --out ./demo

# Validate any bundle — including one written by hand.
autocodabench validate ./demo/demo-ai-text-detection.zip

# List what is checked, by tier, with citations.
autocodabench checks list
```

The validator's checks are organized into three tiers with different
epistemic standing: **deterministic** checks gate (code computes pass or
fail), **LLM-judged** checks advise (findings with rationale, never gates),
and **attestations** surface launch criteria that only a human can certify.
Checks that need context the bundle cannot carry (for example, the
anticipated error rate or the unit of generalization) read a declared
`competition_facts.yaml`; when such facts are absent, the check reports
that it was skipped, together with instructions for enabling it, rather
than silently passing.

## Agentic authoring (bring an LLM backbone)

The authoring pipeline requires an LLM backend and is invoked as follows.

```bash
autocodabench auth status     # which Claude auth path is active, if any
autocodabench plan-build-validate "Plankton image classification, balanced accuracy, \
    two phases" --data ./plankton_sample/

# The model is a slot, not a hard binding — same tools, same audit trail:
autocodabench plan-build-validate "..." --backend ollama:llama3.1      # local, keyless
autocodabench plan-build-validate "..." --backend openai:gpt-4o
autocodabench validate bundle.zip --judged --backend ollama:llama3.1
```

`plan-build-validate` (the `create` alias still works) runs two isolated agent
sessions — plan, then build, then a validation pass — joined only
by a locked, human-editable `implementation_plan.md`. The build agent acts
exclusively through a typed MCP tool surface, so every authoring action is
logged and the finished run is replayable.

**Runtime fidelity through the platform's own container.** The build phase
does not merely lint the bundle; it *executes* the bundle's baseline and
starting-kit notebook inside the competition's declared `docker_image` — the
same image, mounted the same way, that Codabench's worker uses — and iterates
until both run. Two consequences make the result trustworthy. First, when the
self-validation loop must change `docker_image` to obtain a working run (for
instance, moving off a legacy image whose scikit-learn predates a needed
symbol), the *final, proven* image is what `competition.yaml` records and what
`create` reports — so the value uploaded to Codabench is the one already shown
to work, not a guess. Second, autocodabench ships two purpose-built base
images (see [`docker/`](docker/)) — `autocodabench-base-cpu` and
`autocodabench-base-gpu`, derived from the Codabench `py312` and `gpu310`
worker images and pre-loaded with the essential scientific-Python stack and a
known-good notebook toolchain. Using them as the default starting point means
the common case runs with no per-run installation, which both removes a
frequent source of build-time failure and conserves the operator's model
budget.

We support two authentication paths, in order of preference for local use.
If Claude Code is installed and logged in (Pro/Max), no further
configuration is needed: usage draws from the plan's monthly Agent SDK
credit. Otherwise, export `ANTHROPIC_API_KEY`. Hosted multi-user
deployments (such as the HF Space) must use an API key — see
[`docs/INSTRUCTION_FOR_USER.md`](docs/INSTRUCTION_FOR_USER.md).

## Web UI

A Chainlit chat UI (`web/`) wraps the same library in a guided, three-phase
workspace (plan → build → validate). There are two ways to use it.

**Host it yourself.** Copy `.env.example` to `.env` and fill in at least
`SHARED_PASSWORD` (gates the app) and a Claude auth path — a logged-in Claude
Code subscription for local single-user use, or `ANTHROPIC_API_KEY` for any
multi-user deployment. Then:

```bash
pip install -e . && pip install -r web/requirements.txt
cd web && chainlit run app.py --host 127.0.0.1 --port 8500 -h
```

Open <http://127.0.0.1:8500> and sign in with your `SHARED_PASSWORD`. See
[`web/README.md`](web/README.md) for the full operator guide and
[`docs/INSTRUCTION_FOR_USER.md`](docs/INSTRUCTION_FOR_USER.md) §Web UI for the
walkthrough.

**Or try our hosted demo.** Email
[autocodabench@googlegroups.com](mailto:autocodabench@googlegroups.com) and we
will set you up with an account — and a small amount of free credit — to log
in at <https://ktgiahieu-autocodabench-alpha.hf.space/login> and test the full
pipeline without installing anything.

## Documentation pointers

The following table maps reader roles to the relevant documentation.

| Reader | Document |
|--------|----------|
| **Evaluating this software** (demo walkthrough and repository tour) | [`docs/demo-for-reviewers.md`](docs/demo-for-reviewers.md) |
| Asking what is scientifically tested, and how | [`docs/scientific-validation.md`](docs/scientific-validation.md) |
| Using the CLI or library | [`docs/INSTRUCTION_FOR_USER.md`](docs/INSTRUCTION_FOR_USER.md) |
| Trying the Web UI (Space or local `chainlit run`) | [`docs/INSTRUCTION_FOR_USER.md`](docs/INSTRUCTION_FOR_USER.md) §Web UI, then [`web/README.md`](web/README.md) to operate it |
| Working on the package internals | [`docs/architecture.md`](docs/architecture.md) |
| Skill provenance (the origin of each `SKILL.md`) | [`src/autocodabench/skills/<name>/README.md`](src/autocodabench/skills/) |
| The end-to-end benchmarks (any LLM backbone) | [`benchmark/README.md`](benchmark/README.md) |

## Repository layout

The table below summarizes the top-level structure of the repository.

| Path | Contents |
|------|----------|
| `src/autocodabench/` | The library: core authoring, check framework, agent backends, plan→build pipeline, MCP server, CLI. |
| `web/` | Chainlit chat UI — a consumer of the library, deployed by this Space. |
| `benchmark/` | Pure-SDK end-to-end benchmarks (any LLM backbone): create-bench (proposal → working bundle) and validate-bench (seeded-defect detection), with ground-truth competitions and a leakage-controlled, fully reproducible pipeline. |
| `tests/` | Unit suite — fast and fully keyless. |
| `Dockerfile` | Used by HF Spaces to build the image. |

## License

MIT.
