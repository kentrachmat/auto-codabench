"""Declared competition facts — the declare-then-verify side channel.

Many checklist items are unverifiable from the bundle alone ("does the
split match the unit of generalization?" needs to know the grouping
column; the 100/E test-set sizing rule needs the anticipated error rate).
Rather than guessing, those checks consume a small ``competition_facts.yaml``
the organizer (or the generating agent) declares. Checks that need a fact
that is not declared report SKIPPED with instructions for declaring it —
never a silent pass. A skipped check is information; a silently passing
one would be a defect.

Example ``competition_facts.yaml``::

    anticipated_error_rate: 0.05      # expected top-system error → 100/E sizing
    test_set_size: 2400               # rows in the sequestered test set
    unit_of_generalization: patient   # what a split must not straddle
    external_data_allowed: false
    prizes: true                      # money/credits awarded?
    task_type: binary_classification_imbalanced
"""
from __future__ import annotations

from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any

import yaml

FACTS_FILENAME = "competition_facts.yaml"


@dataclass
class CompetitionFacts:
    anticipated_error_rate: float | None = None
    test_set_size: int | None = None
    unit_of_generalization: str | None = None
    external_data_allowed: bool | None = None
    prizes: bool | None = None
    task_type: str | None = None

    @classmethod
    def field_names(cls) -> list[str]:
        return [f.name for f in fields(cls)]

    @classmethod
    def from_yaml(cls, path: str | Path) -> "CompetitionFacts":
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
        if not isinstance(data, dict):
            raise ValueError(f"{path}: top level must be a mapping")
        known = cls.field_names()
        unknown = sorted(set(data) - set(known))
        if unknown:
            raise ValueError(
                f"{path}: unknown fact keys {unknown}; known keys: {known}")
        return cls(**{k: data[k] for k in data})

    @classmethod
    def discover(cls, bundle_dir: Path, explicit: str | Path | None = None) -> "CompetitionFacts":
        """Load from an explicit path, else <bundle>/competition_facts.yaml, else empty."""
        if explicit:
            return cls.from_yaml(explicit)
        candidate = bundle_dir / FACTS_FILENAME
        if candidate.is_file():
            return cls.from_yaml(candidate)
        return cls()

    def to_dict(self) -> dict[str, Any]:
        return {f.name: getattr(self, f.name) for f in fields(self)}
