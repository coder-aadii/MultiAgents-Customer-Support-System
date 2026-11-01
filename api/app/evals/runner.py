from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.database import async_session_factory
from app.models.conversation import Conversation, ConversationStatus
from app.models.visitor import Visitor
from app.providers import FakeLLMProvider, ProviderTimeoutError
from app.schemas.assistant import GeneratorAction, TriageAction
from app.schemas.retrieval import RetrievalResultItem
from app.services.assistant_service import generate_grounded_answer
from app.services.triage_service import classify_message

logger = logging.getLogger("mcss.evals")


@dataclass
class EvalCaseResult:
    case_id: str
    category: str
    passed: bool
    details: str
    provider_calls: int


@dataclass
class EvalReport:
    total: int = 0
    passed: int = 0
    failed: int = 0
    category_summary: dict[str, dict[str, int]] = field(default_factory=dict)
    results: list[EvalCaseResult] = field(default_factory=list)


async def run_evaluation_suite(
    fixture_path: Path | str,
    provider_type: str = "fake",
) -> EvalReport:
    """Run all test cases from the deterministic support_eval fixture.

    Invariants:
    1. Asserts intended action (answer vs handoff).
    2. Exact valid chunk citations for answerable cases.
    3. Factual support relative to fixture evidence.
    4. No answer on required handoff.
    5. Bounded provider calls per turn (<= 2).
    """
    path = Path(fixture_path)
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    cases: list[dict[str, Any]] = data.get("cases", [])
    report = EvalReport(total=len(cases))

    # Setup isolated temporary visitor and conversation for eval runner
    async with async_session_factory() as session:
        vis = Visitor(id=uuid.uuid4(), name="Eval Visitor", email="eval@example.com")
        session.add(vis)
        await session.flush()

        conv = Conversation(
            id=uuid.uuid4(),
            visitor_id=vis.id,
            status=ConversationStatus.open,
        )
        session.add(conv)
        await session.commit()
        conv_id = conv.id

    for case in cases:
        case_id = case["id"]
        category = case["category"]
        expected_action = case["expected_action"]
        query = case["visitor_query"]
        simulate_error = case.get("simulate_error")

        if category not in report.category_summary:
            report.category_summary[category] = {"total": 0, "passed": 0, "failed": 0}
        report.category_summary[category]["total"] += 1

        provider = FakeLLMProvider()

        # Handle error simulation
        if simulate_error == "malformed_json":
            provider.queue_response("Invalid raw JSON payload {")
        elif simulate_error == "timeout":
            provider.queue_error(ProviderTimeoutError("Simulated eval timeout"))

        case_passed = True
        error_reasons: list[str] = []

        async with async_session_factory() as session:
            if category == "faq":
                # Evidence-based generation
                evidence_info = case["evidence"]
                chunk_id = uuid.UUID(evidence_info["chunk_id"])
                doc_id = uuid.UUID(evidence_info["doc_id"])

                retrieved_chunks = [
                    RetrievalResultItem(
                        chunk_id=chunk_id,
                        document_id=doc_id,
                        document_title=evidence_info["doc_title"],
                        rank=1,
                        score=0.92,
                        content=evidence_info["content"],
                        token_estimate=len(evidence_info["content"]) // 4,
                        start_char=0,
                        end_char=len(evidence_info["content"]),
                    )
                ]

                # Setup fixture response citing the exact evidence chunk
                expected_citations = [uuid.UUID(cid) for cid in case["expected_citation_ids"]]
                provider.queue_response({
                    "action": "answer",
                    "answer": f"According to documentation: {evidence_info['content']}",
                    "cited_chunk_ids": [str(c) for c in expected_citations],
                    "confidence": 0.95,
                })

                gen_result = await generate_grounded_answer(
                    db=session,
                    conversation_id=conv_id,
                    visitor_query=query,
                    retrieved_chunks=retrieved_chunks,
                    llm_provider=provider,
                )

                # 1. Action check
                if gen_result.action.value != expected_action:
                    case_passed = False
                    error_reasons.append(
                        f"Expected action '{expected_action}', got '{gen_result.action.value}'"
                    )

                # 2. Citations check
                if set(gen_result.cited_chunk_ids) != set(expected_citations):
                    case_passed = False
                    error_reasons.append(
                        f"Citations mismatch: expected {expected_citations}, got {gen_result.cited_chunk_ids}"
                    )

                # 3. Factual grounding check
                answer_lower = (gen_result.answer or "").lower()
                for keyword in case.get("factual_keywords", []):
                    if keyword.lower() not in answer_lower:
                        case_passed = False
                        error_reasons.append(f"Missing factual keyword '{keyword}' in answer")

                # 4. Confidence heuristic check
                if gen_result.confidence < 0.75:
                    case_passed = False
                    error_reasons.append(f"Confidence {gen_result.confidence} below threshold 0.75")

            else:
                # Triage classification evaluation
                triage_result = await classify_message(
                    db=session,
                    conversation_id=conv_id,
                    current_message=query,
                    llm_provider=provider,
                )

                if not triage_result:
                    case_passed = False
                    error_reasons.append("Triage returned None")
                else:
                    if triage_result.action.value != expected_action:
                        case_passed = False
                        error_reasons.append(
                            f"Expected action '{expected_action}', got '{triage_result.action.value}'"
                        )

            # Provider call bounds check (at most 2 calls per case)
            if provider.call_count > 2:
                case_passed = False
                error_reasons.append(f"Provider calls exceeded bound: {provider.call_count} > 2")

        provider_calls = provider.call_count
        if case_passed:
            report.passed += 1
            report.category_summary[category]["passed"] += 1
            details = "PASS"
        else:
            report.failed += 1
            report.category_summary[category]["failed"] += 1
            details = "; ".join(error_reasons)

        report.results.append(
            EvalCaseResult(
                case_id=case_id,
                category=category,
                passed=case_passed,
                details=details,
                provider_calls=provider_calls,
            )
        )

    return report

