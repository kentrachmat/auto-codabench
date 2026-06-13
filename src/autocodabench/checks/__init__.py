"""Bundle validation framework: registered checks in three tiers.

- deterministic — code computes PASS/FAIL (gates)
- judged        — LLM grades a rubric (advisory findings, never gates)
- attestation   — human-certifiable launch criteria (surfaced, never assumed)

Public surface: :func:`validate_bundle_path` (dir or zip → ValidationReport),
plus the registry primitives for adding custom checks.
"""
from .api import validate_bundle_path, validate_bundle_path_async
from .base import (
    Check,
    CheckContext,
    CheckResult,
    REGISTRY,
    Severity,
    Status,
    Tier,
    register,
)
from .facts import CompetitionFacts
from .report import ValidationReport, checklist_coverage

__all__ = [
    "Check",
    "CheckContext",
    "CheckResult",
    "CompetitionFacts",
    "REGISTRY",
    "Severity",
    "Status",
    "Tier",
    "ValidationReport",
    "checklist_coverage",
    "register",
    "validate_bundle_path",
    "validate_bundle_path_async",
]
