"""Aggregate check results into one report (dict and Markdown renderings).

The report preserves the three-status semantics of the check tiers rather
than collapsing them into one boolean: deterministic FAILs gate (``ok`` is
defined as their absence), judged FINDINGs advise, and
ATTESTATION_REQUIRED items surface as explicit unchecked boxes. Erasing
those distinctions here would undo the epistemic separation the check
framework exists to maintain.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .base import REGISTRY, CheckResult, Status, Tier
from .facts import CompetitionFacts


@dataclass
class ValidationReport:
    bundle_dir: Path
    results: list[CheckResult]
    facts: CompetitionFacts = field(default_factory=CompetitionFacts)

    @property
    def ok(self) -> bool:
        """True iff no deterministic gate failed. Findings and pending
        attestations do not gate — they inform."""
        return not any(r.status == Status.FAIL for r in self.results)

    @property
    def counts(self) -> dict[str, int]:
        return dict(Counter(r.status.value for r in self.results))

    def by_status(self, *statuses: Status) -> list[CheckResult]:
        return [r for r in self.results if r.status in statuses]

    def to_dict(self) -> dict[str, Any]:
        return {
            "bundle_dir": str(self.bundle_dir),
            "ok": self.ok,
            "counts": self.counts,
            "facts": self.facts.to_dict(),
            "results": [r.to_dict() for r in self.results],
        }

    def to_markdown(self) -> str:
        lines: list[str] = []
        verdict = "✅ PASS" if self.ok else "❌ FAIL"
        lines.append(f"# Bundle validation — {verdict}")
        lines.append("")
        lines.append(f"Bundle: `{self.bundle_dir}`")
        counts = self.counts
        lines.append("Results: " + ", ".join(f"{v} {k}" for k, v in sorted(counts.items())))
        lines.append("")

        def section(title: str, rows: list[CheckResult], note: str | None = None) -> None:
            if not rows:
                return
            lines.append(f"## {title}")
            if note:
                lines.append(f"_{note}_")
            lines.append("")
            for r in rows:
                where = f" `{r.where}`" if r.where else ""
                cite = f" — {r.citation}" if r.citation else ""
                lines.append(f"- **[{r.check_id}]**{where} {r.message}{cite}")
            lines.append("")

        section("Gate failures", self.by_status(Status.FAIL),
                "Deterministic checks that block upload — fix these.")
        section("Findings (advisory)", self.by_status(Status.FINDING),
                "Design risks and LLM-judged observations. They do not gate, "
                "but each one is a known failure mode with a citation.")
        section("Attestations required", self.by_status(Status.ATTESTATION_REQUIRED),
                "Only a human can certify these. Unchecked ≠ done.")
        section("Skipped", self.by_status(Status.SKIPPED),
                "Checks that need declared facts or were inapplicable.")
        section("Passed", self.by_status(Status.PASS))
        return "\n".join(lines)


def checklist_coverage() -> list[dict[str, str]]:
    """The implemented-check inventory: id, tier, title, citation.

    This is the docs/paper 'checklist coverage' table — what the validator
    actually covers, by tier, with sources.
    """
    return [
        {
            "id": c.id,
            "tier": c.tier.value,
            "title": c.title,
            "citation": c.citation or "",
        }
        for c in sorted(REGISTRY.values(), key=lambda c: (c.tier.value, c.id))
    ]
