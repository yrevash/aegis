"""Tests for the Presidio-backed PII engine and its regex fallback.

Proves (a) Presidio is the live engine when installed, detecting the full set of
kinds the pipeline relies on, and (b) the module transparently falls back to the
pure-code regex engine when Presidio is pinned off — never crashing, always redacting.
"""

from __future__ import annotations

import pytest

from aegis.guardrails import _pii_presidio, pii


@pytest.fixture(autouse=True)
def _reset_engine():
    """Reset the cached engine selection around every test (isolation between pins)."""
    pii._reset_engine_cache()
    yield
    pii._reset_engine_cache()


presidio_available = _pii_presidio.is_available()
requires_presidio = pytest.mark.skipif(
    not presidio_available, reason="Presidio/spaCy not installed in this environment"
)


@requires_presidio
def test_presidio_is_the_active_engine():
    assert pii.active_engine() == "presidio"


@requires_presidio
def test_presidio_detects_email_phone_card_ssn_person():
    text = (
        "Contact John Smith at john.smith@example.com or +1 415-555-0132, "
        "card 4111 1111 1111 1111, ssn 123-45-6789."
    )
    kinds = {m.kind for m in pii.scan(text)}
    assert {"EMAIL", "PHONE", "CREDIT_CARD", "SSN", "PERSON"} <= kinds


@requires_presidio
def test_presidio_redact_uses_legacy_placeholder_format():
    masked, kinds = pii.redact("email jane@x.com and ssn 123-45-6789")
    assert kinds == ["EMAIL", "SSN"]
    assert "[REDACTED_EMAIL]" in masked
    assert "[REDACTED_SSN]" in masked
    assert "jane@x.com" not in masked


@requires_presidio
def test_presidio_detects_more_than_regex_person_names():
    # PERSON is the headline upgrade: the legacy regex engine could not catch names.
    kinds = {m.kind for m in pii.scan("The signatory is Alice Johnson.")}
    assert "PERSON" in kinds


@requires_presidio
def test_presidio_spans_are_ordered_and_non_overlapping():
    matches = pii.scan("mail a@b.co, ssn 111-22-3333, card 4111 1111 1111 1111")
    starts = [m.start for m in matches]
    assert starts == sorted(starts)
    for earlier, later in zip(matches, matches[1:], strict=False):
        assert earlier.end <= later.start


def test_regex_fallback_when_presidio_pinned_off(monkeypatch):
    """Pinning AEGIS_PII_ENGINE=regex forces the pure-code engine; contract holds."""
    monkeypatch.setenv("AEGIS_PII_ENGINE", "regex")
    pii._reset_engine_cache()

    assert pii.active_engine() == "regex"

    masked, kinds = pii.redact("reach me at jane@corp.com")
    assert kinds == ["EMAIL"]
    assert "[REDACTED_EMAIL]" in masked

    _, card_kinds = pii.redact("card 4111 1111 1111 1111")
    assert "CREDIT_CARD" in card_kinds
    assert pii.contains_pii("ssn 123-45-6789") is True
    assert pii.redact("nothing to see here") == ("nothing to see here", [])
