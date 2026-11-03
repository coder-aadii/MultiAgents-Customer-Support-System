"""Offline 25-case benchmark for Phase 6 multi-agent safety and routing policy."""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from app.agents.reflection import ReflectionRiskPolicy


@dataclass(frozen=True, slots=True)
class MultiAgentEvalCase:
    case_id: str
    category: str
    customer_request: str
    findings_count: int
    has_conflicts_or_missing_evidence: bool
    has_action_proposal: bool
    backend_high_risk: bool
    reflection_required: bool


@dataclass(frozen=True, slots=True)
class MultiAgentEvalCaseResult:
    case_id: str
    passed: bool
    latency_ms: float
    details: str
    plan: tuple[str, ...] = ()
    tools: tuple[str, ...] = ()
    evidence_references: tuple[str, ...] = ()
    terminal_action: str = ""
    tokens: int = 0


@dataclass(frozen=True, slots=True)
class ExecutedWorkflowCase:
    """Normalized observation from a feature-gated worker workflow case."""

    passed: bool
    plan: tuple[str, ...] = ()
    tools: tuple[str, ...] = ()
    evidence_references: tuple[str, ...] = ()
    terminal_action: str = ""
    tokens: int = 0
    details: str = ""


@dataclass(slots=True)
class MultiAgentEvalReport:
    total: int = 0
    passed: int = 0
    failed: int = 0
    p95_latency_ms: float = 0.0
    estimated_reflection_tokens: int = 0
    results: list[MultiAgentEvalCaseResult] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class Phase7ReleaseGate:
    """Explicit release-gate evidence; a missing check can never yield sign-off."""

    benchmark_passed: bool
    security_suite_passed: bool
    provider_and_budget_suite_passed: bool
    database_regression_passed: bool
    ready: bool
    blockers: tuple[str, ...]


def assess_phase7_release_gate(
    benchmark: MultiAgentEvalReport,
    security_suite_passed: bool,
    provider_and_budget_suite_passed: bool,
    database_regression_passed: bool,
) -> Phase7ReleaseGate:
    benchmark_passed = (
        benchmark.total == 25
        and benchmark.passed == 25
        and benchmark.failed == 0
        and benchmark.p95_latency_ms <= MultiAgentBenchmarkRunner.MAX_P95_LATENCY_MS
    )
    checks = {
        "benchmark": benchmark_passed,
        "security_suite": security_suite_passed,
        "provider_and_budget_suite": provider_and_budget_suite_passed,
        "database_regression": database_regression_passed,
    }
    blockers = tuple(name for name, passed in checks.items() if not passed)
    return Phase7ReleaseGate(
        benchmark_passed=benchmark_passed,
        security_suite_passed=security_suite_passed,
        provider_and_budget_suite_passed=provider_and_budget_suite_passed,
        database_regression_passed=database_regression_passed,
        ready=not blockers,
        blockers=blockers,
    )


def default_multi_agent_cases() -> tuple[MultiAgentEvalCase, ...]:
    """Twenty-five deterministic scenarios spanning normal, risky, and unsafe requests."""
    raw_cases = (
        ("greeting", "Hello", 0, False, False, False, False),
        ("faq", "What are your shipping hours?", 1, False, False, False, False),
        ("order_status", "Where is my order?", 1, False, False, False, False),
        ("shipment_status", "What is the status of tracking TRK-99012?", 1, False, False, False, False),
        ("multi_intent", "My order is delayed and my refund is missing.", 2, False, False, False, True),
        ("order_policy", "My order is delayed and what is your delivery policy?", 2, False, False, False, True),
        ("billing", "Why was I charged twice?", 1, False, False, False, True),
        ("refund", "Am I eligible for a refund?", 1, False, False, False, True),
        ("compensation", "Can I receive compensation for a late delivery?", 2, False, False, False, True),
        ("invoice", "Please explain this invoice.", 1, False, False, False, True),
        ("account", "Can you change my account email?", 1, False, False, False, True),
        ("policy", "How should the return policy apply to my case?", 2, False, False, False, True),
        ("action", "Please cancel my order.", 1, False, True, False, True),
        ("store_credit", "Please issue store credit.", 2, False, True, False, True),
        ("conflict", "Where is my shipment?", 2, True, False, False, True),
        ("missing_evidence", "Can you investigate my refund?", 2, True, False, False, True),
        ("backend_risk", "What is happening?", 1, False, False, True, True),
        ("three_domains", "Check my order, shipment, and refund.", 3, False, False, False, True),
        ("eligibility", "Am I eligible for free express shipping?", 2, False, False, False, True),
        ("payment_policy", "What payment policy applies to this charge?", 2, False, False, False, True),
        ("sensitive_decision", "Can you approve my account exception?", 2, False, False, False, True),
        ("refund_action", "Start a refund for my order.", 2, False, True, False, True),
        ("carrier_and_fee", "My carrier is late and I paid express delivery.", 2, False, False, False, True),
        ("incomplete_order", "Investigate my order without an order number.", 2, True, False, False, True),
        ("high_risk_policy", "Interpret this warranty policy for a compensation decision.", 2, False, False, True, True),
    )
    return tuple(
        MultiAgentEvalCase(
            case_id=f"ma_{index:02d}_{category}",
            category=category,
            customer_request=request,
            findings_count=findings,
            has_conflicts_or_missing_evidence=conflict,
            has_action_proposal=action,
            backend_high_risk=high_risk,
            reflection_required=expected,
        )
        for index, (category, request, findings, conflict, action, high_risk, expected) in enumerate(
            raw_cases, start=1
        )
    )


class MultiAgentBenchmarkRunner:
    """Measures deterministic Phase 6 decision coverage without network or database access."""

    MAX_P95_LATENCY_MS = 6_000.0
    MAX_REFLECTION_TOKENS_PER_CASE = 512

    def run(self, cases: tuple[MultiAgentEvalCase, ...] | None = None) -> MultiAgentEvalReport:
        """Run the deterministic routing unit benchmark (not an E2E sign-off)."""
        scenarios = cases or default_multi_agent_cases()
        report = MultiAgentEvalReport(total=len(scenarios))
        latencies: list[float] = []
        for case in scenarios:
            started = time.perf_counter()
            decision = ReflectionRiskPolicy.decide(
                customer_request=case.customer_request,
                findings_count=case.findings_count,
                has_conflicts_or_missing_evidence=case.has_conflicts_or_missing_evidence,
                has_action_proposal=case.has_action_proposal,
                backend_high_risk=case.backend_high_risk,
            )
            latency_ms = (time.perf_counter() - started) * 1000
            latencies.append(latency_ms)
            passed = decision.required is case.reflection_required
            report.results.append(
                MultiAgentEvalCaseResult(
                    case_id=case.case_id,
                    passed=passed,
                    latency_ms=latency_ms,
                    details="PASS" if passed else f"expected={case.reflection_required}, got={decision.required}",
                )
            )
            if decision.required:
                report.estimated_reflection_tokens += self.MAX_REFLECTION_TOKENS_PER_CASE
            report.passed += int(passed)
            report.failed += int(not passed)
        if latencies:
            ordered = sorted(latencies)
            report.p95_latency_ms = ordered[min(len(ordered) - 1, int(len(ordered) * 0.95))]
        return report

    async def run_executable_workflows(
        self,
        execute_case: Callable[[MultiAgentEvalCase], Awaitable[ExecutedWorkflowCase]],
        cases: tuple[MultiAgentEvalCase, ...] | None = None,
    ) -> MultiAgentEvalReport:
        """Execute all cases through a caller-provided feature-gated worker fixture.

        The fixture owns its isolated database, fake provider, and connector data;
        this runner records the observable workflow artefacts needed for release
        evidence rather than mistaking routing-policy timing for end-to-end timing.
        """
        scenarios = cases or default_multi_agent_cases()
        report = MultiAgentEvalReport(total=len(scenarios))
        latencies: list[float] = []
        for case in scenarios:
            started = time.perf_counter()
            outcome = await execute_case(case)
            latency_ms = (time.perf_counter() - started) * 1000
            latencies.append(latency_ms)
            report.results.append(MultiAgentEvalCaseResult(
                case_id=case.case_id,
                passed=outcome.passed,
                latency_ms=latency_ms,
                details=outcome.details or ("PASS" if outcome.passed else "workflow assertion failed"),
                plan=outcome.plan,
                tools=outcome.tools,
                evidence_references=outcome.evidence_references,
                terminal_action=outcome.terminal_action,
                tokens=outcome.tokens,
            ))
            report.passed += int(outcome.passed)
            report.failed += int(not outcome.passed)
            report.estimated_reflection_tokens += outcome.tokens
        if latencies:
            ordered = sorted(latencies)
            report.p95_latency_ms = ordered[min(len(ordered) - 1, int(len(ordered) * 0.95))]
        return report
