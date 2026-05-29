# competition-design — provenance & maintenance

**Skill kind:** knowledge / reference (cited by the two driver skills).
**File:** [`SKILL.md`](./SKILL.md) (~340 lines, ~600 actionable bullets).

## What it contains

A decision tree across nine sections:

| § | Section |
|---|---|
| 0a | Live tensions in the competition-design literature (Phase-1 conversation accelerators) |
| 0  | How to quote from this skill in user-facing chat (the *scientific tone* rule) |
| 1  | Task framing — 5W taxonomy, CTF, λ/α/β/γ submission protocols |
| 2  | Dataset design — splits, leakage audits, FAIR + datasheets, confidential-data tactics |
| 3  | Metric selection — task → primary/secondary metrics table, paired tests, CI sizing |
| 4  | Baseline / starting kit — trivial + competent baselines, sub-baseline filtering |
| 5  | Phases — two-phase rule, the Ladder mechanism, submission caps |
| 6  | Anti-cheating — γ-protocol, probing detection, re-execution policy |
| 7  | Leaderboard hygiene — Borda vs mean, Gibbard's theorem, error bars |
| 8  | Common pitfalls + smell-test checklist (run before launch) |
| 9  | Post-competition — 1-year freeze, datasheet release, lessons-learned paper |

Plus five worked specialisations (`adapt_to_context:` block) for: AI text
detection, image classification, NLP generation, tabular regression,
reinforcement learning.

## Provenance

Every bullet is sourced from
**Pavão et al., *AI Competitions and Benchmarks: The Science Behind the Contests* (2024).**
PDF: <https://ai-competitions-book.github.io/ai-competitions-book-full-project.pdf>.

Chapter cites in the form `(Ch. X §Y)` map verbatim to the book's table
of contents. Particular chapters drawn on heavily:

| Skill section | Book chapters |
|---|---|
| §1 Task framing | Ch. 1 (CTF), Ch. 2 (5W taxonomy, protocols) |
| §2 Dataset design | Ch. 3 (splits, leakage, FAIR, datasheets), Ch. 4 (sizing) |
| §3 Metric selection | Ch. 4 (metrics table, CI, paired tests) |
| §4 Baseline / starting kit | Ch. 5 (baselines, daily caps), Ch. 13 (sustainability) |
| §5 Phases | Ch. 5 (two-phase rule, Ladder), Ch. 11 (re-execution) |
| §6 Anti-cheating | Ch. 11 (sequestered test labels), Ch. 12 (γ protocol) |
| §7 Leaderboard hygiene | Ch. 4 (Borda, Gibbard's theorem) |
| §0a Live tensions | aggregated from all chapters — surface in chat |

A handful of cells additionally cite empirical papers (Roelofs et al.
on Kaggle benchmark drift, Blum & Hardt on the Ladder mechanism,
Sadasivan et al. on AI-text-detection ceilings, etc.). The book is the
**primary** source; empirical citations supplement it.

## Why this file exists

Phase 1 (driven by [`autocodabench-plan`](../plan/README.md)) needs a
fast, in-context reference for design decisions — the book itself is
800+ pages and loading it on demand burns tokens. This skill is the
distilled decision tree.

It is **quotable**: the §0 citation rules require every chat claim that
comes from this file to surface a chapter handle (e.g. `Pavão Ch. 4
§4.2`) so the user can verify against the source. Researchers will
check; the rule treats them like reviewers.

## Editing rules

When updating a bullet:

1. **Cite the chapter section the rule comes from.** Verbatim — don't
   paraphrase chapter titles or invent section numbers.
2. **If you state a rule that isn't in the book**, prefix with
   `Extrapolating from (Ch. X):` so the user can tell where the book
   ends and inference begins. (This rule is §0 of the skill itself.)
3. **Tensions (§0a)** are claims where the book OR a paper takes a
   position; cite both sides with their OpenAlex handles or chapter
   references so Phase 1 can surface the disagreement in chat.

## Pointers

- Upstream source: [Pavão et al. (2024)][book]
- Used by Phase 1: [`autocodabench-plan`](../plan/README.md) — directly
  cites this skill's §§1-7 to motivate plan sections.
- Used by Phase 2: [`autocodabench-implement`](../autocodabench-implement/README.md)
  — occasionally, when picking a default for a field the plan left
  ambiguous (e.g. metric defaults from §3).
- Package map: [`auto_codabench/README.md`](../../README.md)

[book]: https://ai-competitions-book.github.io/ai-competitions-book-full-project.pdf
