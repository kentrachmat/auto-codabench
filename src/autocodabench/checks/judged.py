"""Judged checks — an LLM grades a rubric, through the same AgentBackend seam.

Judged verdicts are *advisory by construction*: they emit FINDINGs, never
PASS/FAIL gates. "Valid" is defined by executable checks, not by a model's
self-assessment; what a judge buys is coverage of semantic properties code
cannot see (do the pages contradict the config? is the metric direction
documented?).

Each judged check builds one rubric prompt, runs it as a tool-less backend
session, and parses a strict-JSON verdict. Unparseable output degrades to a
SKIPPED result — never to a silent pass.
"""
from __future__ import annotations

import json
import re
from typing import Any

from ..backends.base import AgentBackend, AgentTask
from .base import Check, CheckContext, CheckResult, Severity, Status, Tier, register

_MAX_PAGE_CHARS = 16_000
_MAX_YAML_CHARS = 8_000


class JudgedCheck(Check):
    tier = Tier.JUDGED

    def build_prompt(self, ctx: CheckContext) -> str:  # pragma: no cover
        raise NotImplementedError

    async def run_judged(self, ctx: CheckContext, backend: AgentBackend) -> list[CheckResult]:
        missing = self.missing_facts(ctx)
        if missing:
            return [self.skipped(f"requires facts not provided: {', '.join(missing)}")]
        prompt = self.build_prompt(ctx)
        result = await backend.run(AgentTask(prompt=prompt, allowed_tools=[]))
        if not result.ok:
            return [self.skipped(f"judge run failed: {result.error or result.status}")]
        return self.parse_verdict(result.final_text)

    def parse_verdict(self, text: str) -> list[CheckResult]:
        blob = _extract_json(text)
        if blob is None or "findings" not in blob:
            return [self.skipped("judge returned no parseable JSON verdict")]
        findings = blob.get("findings") or []
        if not findings:
            return [self.passed("judge found no issues (advisory)")]
        out: list[CheckResult] = []
        for f in findings:
            if not isinstance(f, dict):
                continue
            out.append(self.finding(str(f.get("message", "")),
                                    where=str(f.get("where")) if f.get("where") else None))
        return out or [self.passed("judge found no issues (advisory)")]


def _extract_json(text: str) -> dict[str, Any] | None:
    """Pull the first JSON object out of the model's reply (fenced or bare)."""
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    candidates = [fenced.group(1)] if fenced else []
    brace = text.find("{")
    if brace != -1:
        candidates.append(text[brace: text.rfind("}") + 1])
    for cand in candidates:
        try:
            data = json.loads(cand)
            if isinstance(data, dict):
                return data
        except json.JSONDecodeError:
            continue
    return None


def _bundle_texts(ctx: CheckContext) -> tuple[str, str]:
    yaml_text = ""
    yaml_path = ctx.bundle_dir / "competition.yaml"
    if yaml_path.is_file():
        yaml_text = yaml_path.read_text(encoding="utf-8", errors="replace")[:_MAX_YAML_CHARS]
    pages = []
    pages_dir = ctx.bundle_dir / "pages"
    if pages_dir.is_dir():
        for p in sorted(pages_dir.glob("*.md")):
            pages.append(f"### {p.name}\n\n" + p.read_text(encoding="utf-8", errors="replace"))
    return yaml_text, "\n\n".join(pages)[:_MAX_PAGE_CHARS]


@register
class DocsConfigConsistency(JudgedCheck):
    """Do the participant-facing pages contradict the machine config?

    The classic failure: pages say "5 submissions per day", the YAML enforces
    10; pages say higher-is-better, the leaderboard sorts ascending. These
    ship silently and surface as participant disputes.
    """

    id = "judged-docs-config-consistency"
    title = "Pages ↔ competition.yaml consistency (LLM-judged, advisory)"
    severity = Severity.WARNING
    citation = "Pavão et al. (Ch. 11, Ch. 13)"

    def build_prompt(self, ctx: CheckContext) -> str:
        yaml_text, pages_text = _bundle_texts(ctx)
        return f"""You are auditing a Codabench competition bundle before launch.

Compare the machine configuration (competition.yaml) against the
participant-facing pages. Report every CONTRADICTION between what the pages
promise and what the config enforces. Look specifically at:

- phase names, dates, and durations
- submission limits (per-day and total)
- metric names and ranking direction (higher- vs lower-is-better vs the
  leaderboard `sorting`)
- submission format the pages describe vs what the scoring program implies
- prizes, rules, or data access promised in pages but absent from config

Only report contradictions you can quote from both sides. Do not report
style issues or missing information — contradictions only.

Respond with ONLY a JSON object, no other text:
{{"findings": [{{"where": "<page or yaml locator>", "message": "<contradiction, quoting both sides>"}}]}}

If there are no contradictions: {{"findings": []}}

--- competition.yaml ---
{yaml_text}

--- pages ---
{pages_text}
"""


async def run_judged_checks(ctx: CheckContext, backend: AgentBackend) -> list[CheckResult]:
    from .base import REGISTRY

    results: list[CheckResult] = []
    for check in REGISTRY.values():
        if isinstance(check, JudgedCheck):
            results.extend(await check.run_judged(ctx, backend))
    return results
