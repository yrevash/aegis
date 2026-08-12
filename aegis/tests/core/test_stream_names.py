from aegis.core import stream_names as n


def test_names_present_and_in_all():
    for name in (n.REASONING, n.GUARDRAIL_VERDICT, n.GUARDRAIL_CACHE, n.SHAP_EXPLANATION,
                 n.CONFORMAL_INTERVAL, n.RETRIEVAL_CITATIONS, n.RETRIEVAL_CACHE, n.ROUTING,
                 n.MEMORY_RECALL):
        assert name in n.ALL
    assert n.is_known(n.REASONING) and not n.is_known("nope")
