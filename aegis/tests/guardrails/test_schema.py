from aegis.guardrails import schema


def test_empty_input_blocked():
    assert schema.validate_input_format("").ok is False


def test_content_filter_flags_leak_marker():
    assert schema.content_filter("... <|im_start|> ...").ok is False
