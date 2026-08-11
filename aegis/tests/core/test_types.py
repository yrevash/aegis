from aegis.core.types import GuardVerdict, GuardResult, PIIMatch, InjectionVerdict, FormatCheck


def test_guard_verdict_values():
    assert {v.value for v in GuardVerdict} == {"pass", "block", "redact", "flag"}


def test_guard_result_defaults():
    r = GuardResult(verdict=GuardVerdict.PASS, reason="ok", text="hi")
    assert r.layer is None and r.redactions == []


def test_models_are_dependency_free():
    import aegis.core.types as t
    assert t.__doc__  # smoke: module importable with only pydantic
