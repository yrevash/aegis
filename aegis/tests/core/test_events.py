from aegis.core.events import GuardrailEvent, SpanKind, StepStarted


def test_span_kinds_cover_openinference():
    for k in ("LLM", "RETRIEVER", "RERANKER", "TOOL", "GUARDRAIL", "AGENT", "CHAIN", "EVALUATOR"):
        assert hasattr(SpanKind, k)


def test_step_started_discriminator():
    e = StepStarted(
        module_id="guardrails",
        step_id="s1",
        name="guard_input",
        span_kind=SpanKind.GUARDRAIL,
    )
    assert e.type == "step.started"


def test_guardrail_event_shape():
    e = GuardrailEvent(
        module_id="guardrails",
        step_id="s1",
        verdict="block",
        rules=["injection"],
        score=0.9,
        rationale="matched signature",
    )
    assert e.type == "data-guardrail" and e.span_kind == SpanKind.GUARDRAIL
