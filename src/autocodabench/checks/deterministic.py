"""Deterministic checks — code computes the verdict; no LLM, no network.

This is the only tier permitted to gate: ``ValidationReport.ok`` is
defined as the absence of a deterministic FAIL, because a gate must be
reproducible and contestable, and only a code-computed verdict is both.
Citations are chapter handles into Pavão et al. (2024), *AI Competitions
and Benchmarks: The Science Behind the Contests*, matching the
competition-design knowledge skill, or the Codabench bundle schema docs.
"""
from __future__ import annotations

import csv
import math
from datetime import datetime

from ..core.bundle_io import validate_bundle
from .base import Check, CheckContext, CheckResult, Severity, Status, Tier, register

_DATE_FORMATS = ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d")


def _parse_date(value) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if not isinstance(value, str):
        return None
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(value.strip(), fmt)
        except ValueError:
            continue
    return None


@register
class SchemaLint(Check):
    """The structural gate: competition.yaml parses, every referenced file
    exists, programs carry runnable metadata, leaderboard keys are written
    by the scoring program."""

    id = "bundle-schema"
    title = "Bundle schema and file references"
    severity = Severity.BLOCKER
    citation = "Codabench bundle schema (Yaml-Structure.md)"

    def run(self, ctx: CheckContext) -> list[CheckResult]:
        report = validate_bundle(ctx.bundle_dir.name, str(ctx.bundle_dir.parent))
        issues = report.get("issues") or []
        if not issues:
            return [self.passed("competition.yaml parses; all referenced files exist; "
                                "programs and leaderboard keys are consistent")]
        out: list[CheckResult] = []
        for issue in issues:
            status = Status.FAIL if issue.get("severity") == "error" else Status.FINDING
            sev = Severity.BLOCKER if issue.get("severity") == "error" else Severity.WARNING
            out.append(self._result(status, issue.get("message", ""),
                                    where=issue.get("where"), severity=sev))
        return out


@register
class TwoPhaseStructure(Check):
    """Single-phase competitions overfit the public leaderboard."""

    id = "two-phase-structure"
    title = "Development + final phase structure"
    citation = "Pavão et al. (Ch. 5, Ch. 11)"

    def run(self, ctx: CheckContext) -> list[CheckResult]:
        phases = ctx.phases()
        if not phases:
            return [self.skipped("no phases declared (schema lint reports this)")]
        if len(phases) >= 2:
            return [self.passed(f"{len(phases)} phases declared (development + final)")]
        return [self.finding(
            "single-phase competition — without a final phase on a private test "
            "set, the public leaderboard is the final ranking and overfits")]


@register
class DevPhaseDuration(Check):
    """A development phase shorter than ~40 days only reaches people who
    were already working on the problem."""

    id = "dev-phase-duration"
    title = "Development phase ≥ 40 days"
    citation = "Pavão et al. (Ch. 13)"

    def run(self, ctx: CheckContext) -> list[CheckResult]:
        phases = ctx.phases()
        if not phases:
            return [self.skipped("no phases declared")]
        first = phases[0]
        start, end = _parse_date(first.get("start")), _parse_date(first.get("end"))
        if start is None or end is None:
            return [self.skipped("first phase start/end not parseable as dates",
                                 where="phases[0]")]
        days = (end - start).days
        if days >= 40:
            return [self.passed(f"development phase runs {days} days", where="phases[0]")]
        return [self.finding(
            f"development phase runs only {days} days — below the ~40-day floor "
            f"for participants who weren't already working on the problem",
            where="phases[0]")]


@register
class DailySubmissionCap(Check):
    """Uncapped development submissions invite brute-force leaderboard
    overfitting; 5–10/day is the typical guard."""

    id = "daily-submission-cap"
    title = "Daily submission cap on development phases"
    citation = "Pavão et al. (Ch. 5)"

    def run(self, ctx: CheckContext) -> list[CheckResult]:
        phases = ctx.phases()
        if len(phases) < 1:
            return [self.skipped("no phases declared")]
        out: list[CheckResult] = []
        dev_phases = phases[:-1] if len(phases) > 1 else phases
        for i, p in enumerate(dev_phases):
            cap = p.get("max_submissions_per_day")
            where = f"phases[{i}]"
            if cap is None:
                out.append(self.finding(
                    f"phase '{p.get('name', i)}' has no max_submissions_per_day — "
                    "uncapped daily submissions enable leaderboard probing", where=where))
            elif isinstance(cap, int) and cap > 10:
                out.append(self.finding(
                    f"phase '{p.get('name', i)}' allows {cap} submissions/day — "
                    "above the typical 5–10 anti-overfitting range", where=where))
            else:
                out.append(self.passed(
                    f"phase '{p.get('name', i)}' caps submissions at {cap}/day", where=where))
        return out


@register
class FinalPhaseSubmissionLimit(Check):
    """The final phase exists to close the overfit loophole: 1–3 total
    submissions on the never-seen private set."""

    id = "final-phase-submission-limit"
    title = "Final phase total-submission limit ≤ 3"
    citation = "Pavão et al. (Ch. 5)"

    def run(self, ctx: CheckContext) -> list[CheckResult]:
        phases = ctx.phases()
        if len(phases) < 2:
            return [self.skipped("no final phase declared (see two-phase-structure)")]
        final = phases[-1]
        where = f"phases[{len(phases) - 1}]"
        limit = final.get("max_submissions")
        if limit is None:
            return [self.finding(
                f"final phase '{final.get('name', '?')}' has no max_submissions — "
                "unlimited final submissions re-open the overfit loophole", where=where)]
        if isinstance(limit, int) and limit <= 3:
            return [self.passed(f"final phase allows {limit} total submissions", where=where)]
        return [self.finding(
            f"final phase allows {limit} total submissions — above the 1–3 norm",
            where=where)]


@register
class LeaderboardSortingDeclared(Check):
    """Every ranked column must declare its direction; a missing `sorting`
    silently inverts metrics where lower is better."""

    id = "leaderboard-sorting"
    title = "Leaderboard columns declare sorting direction"
    citation = "Pavão et al. (Ch. 4); Codabench Yaml-Structure.md"

    def run(self, ctx: CheckContext) -> list[CheckResult]:
        if not ctx.comp:
            return [self.skipped("competition.yaml not parseable")]
        out: list[CheckResult] = []
        for i, lb in enumerate(ctx.comp.get("leaderboards") or []):
            if not isinstance(lb, dict):
                continue
            for j, col in enumerate(lb.get("columns") or []):
                if not isinstance(col, dict) or "computation" in col:
                    continue
                where = f"leaderboards[{i}].columns[{j}]"
                if col.get("sorting") in ("asc", "desc"):
                    out.append(self.passed(
                        f"column '{col.get('key')}' sorts {col['sorting']}", where=where))
                else:
                    out.append(self.finding(
                        f"column '{col.get('key')}' declares no sorting direction — "
                        "the ranking direction of the metric is ambiguous", where=where))
        return out or [self.skipped("no leaderboard columns declared")]


@register
class StartingKitPresent(Check):
    """Participants who cannot submit in their first hour mostly never
    submit; the kit is the single biggest participation lever."""

    id = "starting-kit"
    title = "Runnable starting kit shipped"
    citation = "Pavão et al. (Ch. 5, Ch. 13)"

    def run(self, ctx: CheckContext) -> list[CheckResult]:
        kit = ctx.bundle_dir / "starting_kit"
        files = [p for p in kit.rglob("*") if p.is_file()] if kit.is_dir() else []
        if files:
            return [self.passed(f"starting_kit/ ships {len(files)} file(s)")]
        return [self.finding(
            "no starting_kit/ contents — participants have nothing to download, "
            "run, and submit in their first hour")]


@register
class BaselineSolutionsPresent(Check):
    """Two baselines: a trivial one bounds the metric, a competent one
    signals whether there is room above it."""

    id = "baseline-solutions"
    title = "Baseline solutions shipped (trivial + competent)"
    citation = "Pavão et al. (Ch. 5)"

    def run(self, ctx: CheckContext) -> list[CheckResult]:
        sol_root = ctx.bundle_dir / "solutions"
        dirs = [p for p in sol_root.iterdir() if p.is_dir()] if sol_root.is_dir() else []
        declared = (ctx.comp or {}).get("solutions") or []
        if not dirs:
            return [self.finding(
                "no baseline solution under solutions/ — the bundle cannot be "
                "smoke-tested end-to-end and participants have no reference score")]
        if not declared:
            return [self.finding(
                f"solutions/ contains {len(dirs)} folder(s) but competition.yaml "
                "declares no solutions: block — Codabench will not run them",
                where="competition.yaml:solutions")]
        if len(dirs) == 1:
            return [self.finding(
                "one baseline shipped — consider two (a trivial constant/random "
                "baseline to bound the metric, and a competent off-the-shelf one)",
                severity=Severity.INFO)]
        return [self.passed(f"{len(dirs)} baseline solutions shipped and declared")]


@register
class DockerImagePinned(Check):
    """Silent dependency drift breaks reproducibility — pin the image."""

    id = "docker-image-pinned"
    title = "Worker docker image pinned"
    citation = "Pavão et al. (Ch. 11)"

    def run(self, ctx: CheckContext) -> list[CheckResult]:
        if not ctx.comp:
            return [self.skipped("competition.yaml not parseable")]
        image = ctx.comp.get("docker_image")
        if image:
            return [self.passed(f"docker_image pinned: {image}")]
        return [self.finding(
            "no docker_image declared — submissions will run on whatever default "
            "the queue uses, which can change under you",
            where="competition.yaml")]


@register
class TestSetSize(Check):
    """The 100/E rule: to resolve top systems at anticipated error rate E,
    you need roughly 100/E test examples."""

    id = "test-set-size"
    title = "Test set sized for the anticipated error rate (100/E)"
    citation = "Pavão et al. (Ch. 4)"
    requires_facts = ("anticipated_error_rate",)

    def run(self, ctx: CheckContext) -> list[CheckResult]:
        e = ctx.facts.anticipated_error_rate
        assert e is not None  # guaranteed by requires_facts
        if not (0 < e < 1):
            return [self.skipped(f"anticipated_error_rate={e} is not a rate in (0, 1)")]
        needed = math.ceil(100 / e)

        size = ctx.facts.test_set_size
        counted_from = "facts.test_set_size"
        if size is None:
            size = self._count_reference_rows(ctx)
            counted_from = "reference_data row count"
        if size is None:
            return [self.skipped(
                "cannot determine test-set size — declare test_set_size in "
                "competition_facts.yaml")]
        if size >= needed:
            return [self.passed(
                f"test set has {size} examples ({counted_from}) ≥ 100/E = {needed} "
                f"for E={e}")]
        return [self.finding(
            f"test set has {size} examples ({counted_from}) but the 100/E rule "
            f"needs ≥ {needed} for anticipated error rate {e} — score differences "
            "near the top will be noise")]

    @staticmethod
    def _count_reference_rows(ctx: CheckContext) -> int | None:
        ref = ctx.bundle_dir / "reference_data"
        if not ref.is_dir():
            return None
        csvs = sorted(ref.glob("*.csv"))
        if len(csvs) != 1:
            return None  # ambiguous — require the declared fact
        with csvs[0].open(newline="", encoding="utf-8", errors="replace") as f:
            return sum(1 for row in csv.reader(f) if row)


@register
class ExternalDataRuleStated(Check):
    """Undeclared external data is the most common post-hoc disqualification
    fight; the rule must be written down either way."""

    id = "external-data-rule"
    title = "External-data rule declared and documented"
    citation = "Pavão et al. (Ch. 5)"
    requires_facts = ("external_data_allowed",)

    def run(self, ctx: CheckContext) -> list[CheckResult]:
        allowed = ctx.facts.external_data_allowed
        # Deterministic half: the pages must mention the rule at all.
        pages_dir = ctx.bundle_dir / "pages"
        text = ""
        if pages_dir.is_dir():
            for p in pages_dir.glob("*.md"):
                text += p.read_text(encoding="utf-8", errors="replace").lower()
        if "external data" in text or "external dataset" in text or "pre-trained" in text or "pretrained" in text:
            return [self.passed(
                f"external-data policy (declared: allowed={allowed}) is mentioned "
                "in the competition pages")]
        return [self.finding(
            f"facts declare external_data_allowed={allowed} but no competition "
            "page mentions external data or pre-training — participants will "
            "make incompatible assumptions")]
