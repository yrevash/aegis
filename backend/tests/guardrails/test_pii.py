"""Offline unit tests for the self-built PII detector/redactor.

Pure functions, no network, no model. Covers each detector, the Luhn heuristic
for card numbers, overlap resolution, and the low-false-positive requirement.
"""

from __future__ import annotations

from app.guardrails import pii


def test_email_is_detected_and_redacted():
    redacted, kinds = pii.redact("reach me at john.doe@example.com please")
    assert kinds == ["EMAIL"]
    assert "john.doe@example.com" not in redacted
    assert "[REDACTED_EMAIL]" in redacted


def test_ssn_is_detected():
    redacted, kinds = pii.redact("SSN 123-45-6789 on file")
    assert kinds == ["SSN"]
    assert "[REDACTED_SSN]" in redacted


def test_valid_credit_card_is_detected_via_luhn():
    # 4111 1111 1111 1111 is a well-known Luhn-valid test PAN.
    redacted, kinds = pii.redact("card 4111 1111 1111 1111 expires soon")
    assert "CREDIT_CARD" in kinds
    assert "[REDACTED_CC]" in redacted


def test_luhn_invalid_number_is_not_a_card():
    # 16 digits but fails Luhn -> must not be flagged as a card.
    assert pii._luhn_valid("4111 1111 1111 1112") is False
    _, kinds = pii.redact("order number 1234 5678 9012 3456 shipped")
    assert "CREDIT_CARD" not in kinds


def test_phone_ip_and_aws_key_are_detected():
    _, kinds = pii.redact(
        "call +1 415-555-0132 from 192.168.1.10 using AKIAIOSFODNN7EXAMPLE"
    )
    assert "PHONE" in kinds
    assert "IP_ADDRESS" in kinds
    assert "AWS_ACCESS_KEY" in kinds


def test_api_key_is_detected():
    _, kinds = pii.redact("token sk-abcdEFGH1234abcdEFGH5678 leaked")
    assert "API_KEY" in kinds


def test_clean_text_is_not_redacted():
    text = "The quarterly revenue rose by twelve percent across three regions."
    redacted, kinds = pii.redact(text)
    assert kinds == []
    assert redacted == text


def test_scan_returns_non_overlapping_ordered_matches():
    matches = pii.scan("mail a@b.co and ssn 111-22-3333")
    starts = [m.start for m in matches]
    assert starts == sorted(starts)
    # No two spans overlap.
    for earlier, later in zip(matches, matches[1:], strict=False):
        assert earlier.end <= later.start


def test_contains_pii_boolean_helper():
    assert pii.contains_pii("email x@y.com") is True
    assert pii.contains_pii("no secrets here") is False
