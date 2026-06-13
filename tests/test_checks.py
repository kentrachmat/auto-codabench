"""Tests for the check framework against the rebuilt demo bundle."""
import shutil

import yaml

from autocodabench.checks import (
    CompetitionFacts,
    Status,
    Tier,
    checklist_coverage,
    validate_bundle_path,
)


def _statuses(report, check_id):
    return [r.status for r in report.results if r.check_id == check_id]


def test_demo_bundle_passes_gates(demo_bundle):
    report = validate_bundle_path(demo_bundle)
    assert report.ok
    assert Status.PASS in _statuses(report, "bundle-schema")


def test_zip_validation_equivalent(demo_bundle, tmp_path):
    zip_path = demo_bundle.parent / f"{demo_bundle.name}.zip"
    assert zip_path.is_file()
    report = validate_bundle_path(zip_path)
    assert report.ok


def test_schema_failure_gates(demo_bundle):
    (demo_bundle / "pages" / "overview.md").unlink()
    report = validate_bundle_path(demo_bundle)
    assert not report.ok
    assert Status.FAIL in _statuses(report, "bundle-schema")


def test_missing_starting_kit_is_finding_not_gate(demo_bundle):
    shutil.rmtree(demo_bundle / "starting_kit")
    report = validate_bundle_path(demo_bundle)
    assert report.ok  # advisory, not a gate
    assert Status.FINDING in _statuses(report, "starting-kit")


def test_single_phase_finding(demo_bundle):
    comp_path = demo_bundle / "competition.yaml"
    comp = yaml.safe_load(comp_path.read_text())
    comp["phases"] = comp["phases"][:1]
    comp_path.write_text(yaml.safe_dump(comp, sort_keys=False))
    report = validate_bundle_path(demo_bundle)
    assert Status.FINDING in _statuses(report, "two-phase-structure")
    assert Status.SKIPPED in _statuses(report, "final-phase-submission-limit")


def test_uncapped_dev_phase_finding(demo_bundle):
    comp_path = demo_bundle / "competition.yaml"
    comp = yaml.safe_load(comp_path.read_text())
    comp["phases"][0].pop("max_submissions_per_day")
    comp_path.write_text(yaml.safe_dump(comp, sort_keys=False))
    report = validate_bundle_path(demo_bundle)
    assert Status.FINDING in _statuses(report, "daily-submission-cap")


def test_facts_gate_skips_without_facts(demo_bundle):
    (demo_bundle / "competition_facts.yaml").unlink()
    report = validate_bundle_path(demo_bundle)
    assert _statuses(report, "test-set-size") == [Status.SKIPPED]
    assert _statuses(report, "external-data-rule") == [Status.SKIPPED]


def test_test_set_size_uses_declared_facts(demo_bundle, tmp_path):
    facts = tmp_path / "facts.yaml"
    facts.write_text("anticipated_error_rate: 0.2\ntest_set_size: 1000\n")
    report = validate_bundle_path(demo_bundle, facts_path=facts)
    assert _statuses(report, "test-set-size") == [Status.PASS]


def test_test_set_size_flags_undersized_set(demo_bundle):
    # The shipped facts declare E=0.2 → needs ≥500; the toy set has 40 rows.
    report = validate_bundle_path(demo_bundle)
    assert _statuses(report, "test-set-size") == [Status.FINDING]


def test_attestations_always_surface(demo_bundle):
    report = validate_bundle_path(demo_bundle)
    attested = [r for r in report.results if r.status == Status.ATTESTATION_REQUIRED]
    assert len(attested) >= 3
    # prizes=false in the shipped facts resolves the game-of-skill attestation
    assert _statuses(report, "attest-game-of-skill") == [Status.PASS]


def test_unknown_fact_key_rejected(tmp_path):
    bad = tmp_path / "facts.yaml"
    bad.write_text("not_a_fact: 1\n")
    try:
        CompetitionFacts.from_yaml(bad)
        raise AssertionError("expected ValueError")
    except ValueError as e:
        assert "unknown fact keys" in str(e)


def test_checklist_coverage_lists_all_tiers():
    rows = checklist_coverage()
    tiers = {r["tier"] for r in rows}
    assert tiers == {t.value for t in Tier}
    assert all(r["citation"] for r in rows)


def test_report_markdown_renders(demo_bundle):
    report = validate_bundle_path(demo_bundle)
    md = report.to_markdown()
    assert "Bundle validation" in md
    assert "Attestations required" in md
