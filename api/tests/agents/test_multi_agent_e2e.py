from app.evals.multi_agent_runner import (
    MultiAgentBenchmarkRunner,
    assess_phase7_release_gate,
    default_multi_agent_cases,
)


def test_evaluation_benchmark_score_100() -> None:
    cases = default_multi_agent_cases()
    report = MultiAgentBenchmarkRunner().run(cases)

    assert len(cases) == 25
    assert report.total == 25
    assert report.passed == 25
    assert report.failed == 0
    assert report.p95_latency_ms <= MultiAgentBenchmarkRunner.MAX_P95_LATENCY_MS
    assert report.estimated_reflection_tokens <= 25 * MultiAgentBenchmarkRunner.MAX_REFLECTION_TOKENS_PER_CASE

    blocked_gate = assess_phase7_release_gate(report, True, True, False)
    assert blocked_gate.ready is False
    assert blocked_gate.blockers == ("database_regression",)

    ready_gate = assess_phase7_release_gate(report, True, True, True)
    assert ready_gate.ready is True
