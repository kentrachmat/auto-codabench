# plan / `autocodabench-plan` — Phase 1 driver

**Skill kind:** driver (subagent orchestrator).
**Skill name:** `autocodabench-plan` (the directory is `plan/`; the skill's
`name:` frontmatter is what Claude indexes).
**File:** [`SKILL.md`](./SKILL.md).

## What it does

Drives Phase 1 of an AutoCodabench session:

1. Opens an MCP run (`autocodabench_open_run`) — first tool call, always.
2. Has a short, citation-grounded roadmap conversation with the user
   (target 3–6 turns total).
3. Produces ONE artifact:
   `<run>/specs/implementation_plan.md` (saved via
   `autocodabench_snapshot_spec`).
4. Hands off to Phase 2 with a stop-and-wait message — the user clicks
   **▶ Advance to Phase 2 — Competition Creation** in the phase bar.

The plan covers the seven design sections (task / data / metric /
baseline / rules / ethics / schedule) at sufficient concreteness that
Phase 2 can package a working Codabench bundle from it WITHOUT seeing
the Phase 1 chat history.

## Why it's a *driver*, not a *knowledge* skill

The "knowledge" lives in the two reference skills:

- [`competition-design`](../competition-design/README.md) — Pavão book
  decision tree, used to motivate design choices and surface tensions
  (§0a) during the roadmap conversation.
- [`codabench-bundle`](../codabench-bundle/README.md) — Codabench
  schema, consulted occasionally for vocabulary (e.g. λ vs γ protocol
  names, phase field names).

This skill is the **orchestration layer**: it sequences the
conversation, enforces hard rules (no code in Phase 1, no `nb_*`
notebook tools, citations as clickable markdown links), and shapes the
plan markdown into a template Phase 2 can parse by header.

## Design rationale — why Phase 1 / Phase 2 are split

The web app discards the entire `ClaudeSDKClient` between Phase 1 and
Phase 2 (see [`web/app.py`](../../../web/app.py) →
`_advance_to_phase`). The reasons:

| Concern | Effect of phase isolation |
|---|---|
| **Cost** | Phase 2 doesn't pay to re-read Phase 1's chat history every turn. Input tokens grow ~quadratically with turn count in one long session; a hard reset bounds the bill. |
| **Context window** | Phase 2 starts with ~0 input tokens, leaving room for the full bundle generation without compaction. |
| **Tool scope** | Phase 1's allowlist forbids `mcp__autocodabench__write_*`, `init_bundle`, `validate_bundle`, `zip_bundle`. Phase 2's allowlist forbids `snapshot_spec` (the plan is locked). Tight allowlists shrink the tool-use search space and reduce wrong-tool calls. |
| **Auditability** | The plan markdown is the **only carrier** between phases. Every Phase 2 decision either traces to a line in the plan or is logged as a "decision under ambiguity" in the bundle. |

The trade-off: anything important the user said in Phase 1 chat but
DIDN'T end up in the plan is gone. The hard rule "be specific — named
sklearn classes, formal metric names" in §0 of this skill exists
specifically to push the planner to write everything down.

## How this skill was generated

- **The §1 roadmap table** (7 sections) is derived from
  [`competition-design`](../competition-design/README.md) §§1–7 — the
  sections of that skill are the sections of the plan, 1:1.
- **The §2 template** is engineered for Phase 2 parseability. The
  headings `## 1. Task formulation` through `## 7. Schedule &
  sustainability` are load-bearing because Phase 2 looks them up by
  exact string match. Don't rename the headers without updating Phase 2.
- **The §0 hard-rule list** is the codification of failure modes
  observed during early end-to-end runs:
  - vague language ("an appropriate model") causing Phase 2 to invent
    details the user wouldn't have approved;
  - missed `open_run` calls orphaning the run dir under
    `runs/LATEST` while logs went elsewhere;
  - `nb_*` notebook tool calls left over from the legacy 3-phase
    starting-kit flow (now removed; see
    [`try-web-ui-with-starting-kit`](https://github.com/ihsaan-ullah/auto-codabench/tree/try-web-ui-with-starting-kit)
    for the archived variant);
  - bare `[oa:Wxxxxx]` OpenAlex handles instead of clickable links.
- **The hand-off message** (§3) explicitly tells the user the next
  phase will start blind, so they can revise the plan *now* if
  something important was discussed but not written.

## Pointers

- Knowledge it cites: [`competition-design`](../competition-design/README.md), [`codabench-bundle`](../codabench-bundle/README.md)
- Hands off to: [`autocodabench-implement`](../autocodabench-implement/README.md)
- MCP tools used: `autocodabench_open_run`, `_current_run`,
  `_log_event`, `_snapshot_spec`
  — see [package README → MCP tools](../../README.md#mcp-tools).
- Phase orchestrator in code:
  [`web/app.py`](../../../web/app.py) → `_advance_to_phase`
- Phase 1 produces: `<run>/specs/implementation_plan.md`
- Package map: [`docs/architecture.md`](../../../../docs/architecture.md)
