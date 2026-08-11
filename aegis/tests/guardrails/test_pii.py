from aegis.guardrails import pii


def test_redacts_email_and_reports_kind():
    red, kinds = pii.redact("mail me at a@b.com")
    assert "[REDACTED_EMAIL]" in red and kinds == ["EMAIL"]


def test_clean_text_untouched():
    assert pii.redact("hello world") == ("hello world", [])
