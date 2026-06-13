"""Attestation items — facts only a human can certify.

These are real launch criteria from the competition-design checklist that no
amount of code or LLM judgment can verify. The validator surfaces them as
unchecked boxes so they are impossible to forget, and never pretends to have
checked them.
"""
from __future__ import annotations

from .base import Check, CheckContext, CheckResult, Severity, Tier, register


class _Attestation(Check):
    tier = Tier.ATTESTATION
    severity = Severity.WARNING
    statement: str = ""

    def run(self, ctx: CheckContext) -> list[CheckResult]:
        return [self.attestation(self.statement)]


@register
class ExternalReviewAttestation(_Attestation):
    id = "attest-external-review"
    title = "External proposal review"
    citation = "Pavão et al. (Ch. 2)"
    statement = ("At least one external reviewer (ideally 3+) attempted the task "
                 "before announcement — one of the four pillars of successful "
                 "challenges, and the cheapest dead-end-task catch available.")


@register
class LeakageProbeAttestation(_Attestation):
    id = "attest-leakage-probe"
    title = "Per-feature leakage probe"
    citation = "Pavão et al. (Ch. 3)"
    statement = ("A model was trained on each candidate leaky feature alone and "
                 "confirmed not to beat the trivial baseline (covers ground-truth-"
                 "in-features, duplicate entities, and processing leakage).")


@register
class DatasheetAttestation(_Attestation):
    id = "attest-datasheet"
    title = "Datasheet / data nutrition label"
    citation = "Pavão et al. (Ch. 3)"
    statement = ("A datasheet (Gebru et al.) covering provenance, consent, known "
                 "biases, and intended use is published with the dataset.")


@register
class DataPersistenceAttestation(_Attestation):
    id = "attest-data-persistence"
    title = "Dataset license and post-competition home"
    citation = "Pavão et al. (Ch. 3, Ch. 13)"
    statement = ("The dataset has an explicit license, a persistent identifier or "
                 "URL, and a decided post-competition home — benchmarks whose data "
                 "dies after the leaderboard close are not benchmarks.")


@register
class GameOfSkillAttestation(_Attestation):
    id = "attest-game-of-skill"
    title = "Prize legality (game of skill)"
    citation = "Pavão et al. (Ch. 13)"
    requires_facts = ("prizes",)
    statement = ("Legal confirmed 'game of skill' jurisdiction rules for the "
                 "prize structure.")

    def run(self, ctx: CheckContext) -> list[CheckResult]:
        if ctx.facts.prizes is False:
            return [self.passed("facts declare prizes=false — no prize-law exposure")]
        return [self.attestation(self.statement)]
