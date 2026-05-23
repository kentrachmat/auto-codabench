# AutoCodabench

A scientific-friend chat assistant for designing **Codabench** competitions.

---

## How this app works — 2 phases

The phase bar at the top of the page (next to **Readme**) is your navigation surface. Each phase has its own
fresh agent and the chat history is dropped between phases.

### 1. 📝 Plan *(you start here)*

You interact with the agent until converges on a one-page
`implementation_plan.md` covering all 7 design sections of a Codabench
competition (task, data, metric, baseline, rules, ethics, schedule).

Review the plan in the **workspace panel on the right** (📝
`implementation_plan.md` tab). When it looks right, click the
**▶ Advance to Phase 2** button at the top.

### 2. 📦 Competition Creation

A *fresh* agent reads the implementation plan and packages a Codabench `.zip`
directly:

- `competition.yaml`
- `scoring_program/score.py` — implements your metric
- `solution/sample_code_submission/model.py` — the baseline class from
  the plan
- four standard pages (overview, evaluation, terms, data)

After validation + zip, a 📦 `bundle.zip` tab appears in the
workspace panel for download. A one-click **⬆️ Upload to Codabench**
button also shows up in chat — clicking it publishes the competition
and surfaces the Codabench URL.

### Revise you plan: Back-navigation

Once you're in Phase 2, Phase 1 turns into a 🔒 lock. In order to revise the plan, click
it to **revise the plan** and **discards the current bundle**.---

## Internal note — operator checklist

(_This block is for the project maintainer / internal testers._)

This is a **private alpha** for invited collaborators:

- Logs are uploaded to a private HF Dataset
  repo named `https://huggingface.co/datasets/ktgiahieu/autocodabench-runs/tree/main`
- The session has a hard Anthropic API budget cap (default **$5.00**;
  configurable via `MAX_USD_PER_SESSION`).

### Known limitations

- Phase 2 picks sensible sklearn defaults if the plan doesn't fully
  specify a baseline / metric.
- HF Spaces is CPU-only, ≤16 GB RAM. Curated package whitelist:
  `numpy`, `pandas`, `scikit-learn`, `matplotlib`, `seaborn`, `scipy`,
  `pillow`.
