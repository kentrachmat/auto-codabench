"""Entry points for validating a bundle directory or zip.

The validator deliberately accepts *any* bundle — hand-written, exported
from a production competition, or produced by the pipeline — rather than
only our own output. Validating foreign bundles is both the tool's
standalone value to organizers and the validator's own regression diet:
a validator that only ever sees generated bundles co-evolves with the
generator and stops measuring anything external (see
``docs/design-rationale.md``, Section 6). Importing this module registers
the full check registry as a side effect; a zip argument is unpacked to a
temporary directory and the bundle root located within it.
"""
from __future__ import annotations

import asyncio
import tempfile
import zipfile
from pathlib import Path

from .base import CheckContext, run_checks
from .facts import CompetitionFacts
from .report import ValidationReport

# Importing registers the checks.
from . import attestations as _attestations  # noqa: F401
from . import deterministic as _deterministic  # noqa: F401
from . import judged as _judged  # noqa: F401


def _locate_bundle_root(extracted: Path) -> Path:
    """competition.yaml at the zip root, or inside a single top-level folder
    (the classic zipped-the-containing-folder mistake — still validatable)."""
    if (extracted / "competition.yaml").is_file():
        return extracted
    subdirs = [p for p in extracted.iterdir() if p.is_dir()]
    if len(subdirs) == 1 and (subdirs[0] / "competition.yaml").is_file():
        return subdirs[0]
    return extracted


async def validate_bundle_path_async(
    bundle: str | Path,
    *,
    facts_path: str | Path | None = None,
    judged: bool = False,
    backend=None,
) -> ValidationReport:
    path = Path(bundle).expanduser().resolve()
    if not path.exists():
        raise FileNotFoundError(f"bundle not found: {path}")

    tmp: tempfile.TemporaryDirectory | None = None
    if path.is_file() and path.suffix == ".zip":
        tmp = tempfile.TemporaryDirectory(prefix="acb-validate-")
        with zipfile.ZipFile(path) as zf:
            zf.extractall(tmp.name)
        bundle_dir = _locate_bundle_root(Path(tmp.name))
    elif path.is_dir():
        bundle_dir = path
    else:
        raise ValueError(f"bundle must be a directory or a .zip: {path}")

    try:
        facts = CompetitionFacts.discover(bundle_dir, facts_path)
        ctx = CheckContext.from_bundle_dir(bundle_dir, facts=facts)
        results = run_checks(ctx)
        if judged:
            if backend is None:
                from ..backends import get_claude_backend
                backend = get_claude_backend()
            from .judged import run_judged_checks
            results.extend(await run_judged_checks(ctx, backend))
        # Report the user's original path, not the extraction tempdir.
        return ValidationReport(bundle_dir=path, results=results, facts=facts)
    finally:
        if tmp is not None:
            tmp.cleanup()


def validate_bundle_path(
    bundle: str | Path,
    *,
    facts_path: str | Path | None = None,
    judged: bool = False,
    backend=None,
) -> ValidationReport:
    """Sync wrapper. Inside a running event loop, call the async variant."""
    return asyncio.run(validate_bundle_path_async(
        bundle, facts_path=facts_path, judged=judged, backend=backend))
