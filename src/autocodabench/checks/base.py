"""The Check contract: one validation concern, one registered component.

Three tiers, three different epistemic standings — never conflated:

- ``DETERMINISTIC`` — code computes the verdict. PASS/FAIL gate.
- ``JUDGED`` — an LLM grades a rubric. Emits advisory FINDINGs, never gates.
- ``ATTESTATION`` — only a human can know (external review happened, legal
  signed off). Surfaced as an unchecked box in the report, never silently
  assumed.

Every check cites its source (a Pavão et al. chapter handle or the
Codabench schema), so each line of the report is a cited claim.
"""
from __future__ import annotations

import enum
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .facts import CompetitionFacts


class Tier(str, enum.Enum):
    DETERMINISTIC = "deterministic"
    JUDGED = "judged"
    ATTESTATION = "attestation"


class Severity(str, enum.Enum):
    BLOCKER = "blocker"
    WARNING = "warning"
    INFO = "info"


class Status(str, enum.Enum):
    PASS = "pass"
    FAIL = "fail"                  # deterministic tier only
    FINDING = "finding"            # advisory — judged tier or soft deterministic
    ATTESTATION_REQUIRED = "attestation_required"
    SKIPPED = "skipped"            # missing fact / inapplicable


@dataclass
class CheckResult:
    check_id: str
    status: Status
    severity: Severity
    message: str
    where: str | None = None       # locator inside the bundle, if any
    citation: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "check_id": self.check_id,
            "status": self.status.value,
            "severity": self.severity.value,
            "message": self.message,
            "where": self.where,
            "citation": self.citation,
        }


@dataclass
class CheckContext:
    """Everything a check may look at. Built once per validation run."""

    bundle_dir: Path
    comp: dict[str, Any] | None            # parsed competition.yaml (None if unreadable)
    facts: CompetitionFacts = field(default_factory=CompetitionFacts)

    @classmethod
    def from_bundle_dir(cls, bundle_dir: Path, facts: CompetitionFacts | None = None) -> "CheckContext":
        comp: dict[str, Any] | None = None
        yaml_path = bundle_dir / "competition.yaml"
        if yaml_path.is_file():
            try:
                loaded = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
                comp = loaded if isinstance(loaded, dict) else None
            except yaml.YAMLError:
                comp = None
        return cls(bundle_dir=bundle_dir, comp=comp, facts=facts or CompetitionFacts())

    def phases(self) -> list[dict[str, Any]]:
        if not self.comp:
            return []
        return [p for p in (self.comp.get("phases") or []) if isinstance(p, dict)]


class Check:
    """Base class. Subclass, set the class attrs, implement ``run``."""

    id: str = ""
    title: str = ""
    tier: Tier = Tier.DETERMINISTIC
    severity: Severity = Severity.WARNING
    citation: str | None = None
    # Fact names that must be present in CompetitionFacts; otherwise the
    # check reports SKIPPED with an actionable message instead of guessing.
    requires_facts: tuple[str, ...] = ()

    def run(self, ctx: CheckContext) -> list[CheckResult]:  # pragma: no cover
        raise NotImplementedError

    # -- result helpers -----------------------------------------------------

    def _result(self, status: Status, message: str, *, where: str | None = None,
                severity: Severity | None = None) -> CheckResult:
        return CheckResult(
            check_id=self.id,
            status=status,
            severity=severity or self.severity,
            message=message,
            where=where,
            citation=self.citation,
        )

    def passed(self, message: str, **kw: Any) -> CheckResult:
        return self._result(Status.PASS, message, **kw)

    def failed(self, message: str, **kw: Any) -> CheckResult:
        return self._result(Status.FAIL, message, **kw)

    def finding(self, message: str, **kw: Any) -> CheckResult:
        return self._result(Status.FINDING, message, **kw)

    def skipped(self, message: str, **kw: Any) -> CheckResult:
        return self._result(Status.SKIPPED, message, **kw)

    def attestation(self, message: str, **kw: Any) -> CheckResult:
        return self._result(Status.ATTESTATION_REQUIRED, message, **kw)

    def missing_facts(self, ctx: CheckContext) -> list[str]:
        return [f for f in self.requires_facts if getattr(ctx.facts, f, None) is None]


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

REGISTRY: dict[str, Check] = {}


def register(cls: type[Check]) -> type[Check]:
    """Class decorator: instantiate and register a check by its id."""
    inst = cls()
    if not inst.id:
        raise ValueError(f"{cls.__name__} has no id")
    if inst.id in REGISTRY:
        raise ValueError(f"duplicate check id: {inst.id}")
    REGISTRY[inst.id] = inst
    return cls


def checks_for(tiers: set[Tier] | None = None) -> list[Check]:
    out = [c for c in REGISTRY.values() if tiers is None or c.tier in tiers]
    return sorted(out, key=lambda c: (c.tier.value, c.id))


def run_checks(ctx: CheckContext, tiers: set[Tier] | None = None) -> list[CheckResult]:
    """Run all registered checks for the requested tiers (judged excluded —
    judged checks are async and dispatched by :mod:`autocodabench.checks.api`)."""
    results: list[CheckResult] = []
    for check in checks_for(tiers):
        if check.tier == Tier.JUDGED:
            continue
        missing = check.missing_facts(ctx)
        if missing:
            results.append(check.skipped(
                f"requires facts not provided: {', '.join(missing)} — add them to "
                f"competition_facts.yaml to enable this check"))
            continue
        results.extend(check.run(ctx))
    return results
