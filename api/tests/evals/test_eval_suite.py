"""Pytest suite executing the 30-case support evaluation benchmark."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.evals.runner import run_evaluation_suite


@pytest.mark.asyncio
async def test_support_eval_deterministic_benchmarks():
    """Execute the full 30-case support evaluation benchmark and verify 100% pass rate."""
    fixture_path = Path(__file__).parent.parent / "fixtures" / "support_eval.json"
    assert fixture_path.exists(), f"Fixture file missing: {fixture_path}"

    report = await run_evaluation_suite(fixture_path, provider_type="fake")

    assert report.total == 30
    assert report.failed == 0
    assert report.passed == 30

    # Ensure all required categories exist and pass
    categories = report.category_summary
    assert categories["faq"]["total"] == 10
    assert categories["faq"]["passed"] == 10

    assert categories["human_handoff"]["total"] == 8
    assert categories["human_handoff"]["passed"] == 8

    assert categories["account_order"]["total"] == 5
    assert categories["account_order"]["passed"] == 5

    assert categories["unsafe_domain"]["total"] == 3
    assert categories["unsafe_domain"]["passed"] == 3

    assert categories["irrelevant"]["total"] == 2
    assert categories["irrelevant"]["passed"] == 2

    assert categories["provider_failure"]["total"] == 2
    assert categories["provider_failure"]["passed"] == 2

