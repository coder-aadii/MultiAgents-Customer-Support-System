from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from app.evals.runner import run_evaluation_suite


def main() -> None:
    parser = argparse.ArgumentParser(description="MCSS Support Assistant Evaluator")
    parser.add_argument(
        "--provider",
        choices=["fake", "openai"],
        default="fake",
        help="Target provider (default: fake)",
    )
    parser.add_argument(
        "--fixture",
        default="tests/fixtures/support_eval.json",
        help="Path to evaluation fixture JSON",
    )
    args = parser.parse_args()

    fixture_path = Path(args.fixture)
    if not fixture_path.exists():
        # Fallback to absolute or relative from api root
        alt_path = Path(__file__).parent.parent.parent / "tests" / "fixtures" / "support_eval.json"
        if alt_path.exists():
            fixture_path = alt_path
        else:
            print(f"Error: Fixture file not found at {fixture_path} or {alt_path}")
            sys.exit(1)

    print(f"=== Running MCSS Support Assistant Evaluation Suite ===")
    print(f"Provider: {args.provider}")
    print(f"Fixture:  {fixture_path}")
    print("-" * 65)

    report = asyncio.run(run_evaluation_suite(fixture_path, provider_type=args.provider))

    print(f"{'Category':<22} | {'Total':<6} | {'Passed':<6} | {'Failed':<6} | Rate")
    print("-" * 65)
    for cat, stats in report.category_summary.items():
        rate = (stats["passed"] / stats["total"]) * 100 if stats["total"] > 0 else 0
        print(
            f"{cat:<22} | {stats['total']:<6} | {stats['passed']:<6} | {stats['failed']:<6} | {rate:.1f}%"
        )
    print("-" * 65)
    total_rate = (report.passed / report.total) * 100 if report.total > 0 else 0
    print(
        f"{'TOTAL':<22} | {report.total:<6} | {report.passed:<6} | {report.failed:<6} | {total_rate:.1f}%"
    )

    if report.failed > 0:
        print("\nFailed Cases:")
        for r in report.results:
            if not r.passed:
                print(f"  - [{r.category}] {r.case_id}: {r.details}")
        sys.exit(1)

    print("\n✓ All evaluation benchmarks passed within safety boundaries.")
    sys.exit(0)


if __name__ == "__main__":
    main()

