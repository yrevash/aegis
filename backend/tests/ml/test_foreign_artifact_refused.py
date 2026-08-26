"""A foreign artifact at the spine's path is refused, not served and not crashed on.

``DEFAULT_ARTIFACT_PATH`` is a shared address. A sibling project (``aegis_ml``)
promotes challenger models onto exactly this file as its designed handoff, and any
tool that can write a ``.joblib`` can land there. ``TrustworthyModel.load`` is
``joblib.load`` with a return annotation, and an annotation is not a check.

That is not hypothetical: a promoted ``sklearn.pipeline.Pipeline`` landed here and
``GET /ml/model-card`` answered **500** with ``AttributeError: 'Pipeline' object has
no attribute 'model_card'`` -- a message four frames from the cause that never names
the file. The route's ``FileNotFoundError`` handler could not help, because the file
was present; only its contents were wrong.

These tests pin the two halves of the fix: the refusal fires on the object's
capability rather than its class, and a duck-typed stand-in that genuinely satisfies
the routes is *not* refused for having the wrong ancestor.
"""

from __future__ import annotations

import joblib
import pytest
from aegis.ml import MLModelUnavailableError
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler


class ServableStandIn:
    """Satisfies the routes without inheriting from ``TrustworthyModel``.

    Defined at module scope because ``joblib.dump`` pickles by qualified name, and a
    class declared inside a test function has no importable one.
    """

    def model_card(self):  # noqa: D102 - shape only
        return {"ok": True}

    def predict_explain(self, features):  # noqa: D102, ARG002 - shape only
        return {"ok": True}


def test_a_bare_estimator_is_refused_with_a_message_naming_the_file(tmp_path, monkeypatch):
    """The exact artifact that produced the 500 now produces a typed refusal."""
    import app.ml as host_ml

    artifact = tmp_path / "ml_spine.joblib"
    joblib.dump(Pipeline([("scale", StandardScaler())]), artifact)
    monkeypatch.setattr(host_ml, "_MODEL", None)

    with pytest.raises(MLModelUnavailableError) as excinfo:
        host_ml.load(artifact)

    message = str(excinfo.value)
    assert str(artifact) in message, "the refusal must name the file to fix"
    assert "Pipeline" in message, "it must say what the artifact actually is"
    assert "model_card" in message, "and which part of the contract is missing"
    # The bad object must not become the cached singleton, or the next caller --
    # which does not go through load() -- serves it anyway.
    assert host_ml._MODEL is None


def test_the_contract_is_the_methods_not_the_class(tmp_path, monkeypatch):
    """A stand-in that can serve is accepted; isinstance would have refused it.

    This is the test that keeps the guard from becoming a different bug. The routes
    call two methods, so anything providing them can serve -- and a wrapper, a
    subclass from another module or a future adapter must not be rejected for its
    ancestry.
    """
    import app.ml as host_ml

    artifact = tmp_path / "standin.joblib"
    joblib.dump(ServableStandIn(), artifact)
    monkeypatch.setattr(host_ml, "_MODEL", None)

    loaded = host_ml.load(artifact)

    assert loaded.model_card() == {"ok": True}
    assert host_ml._MODEL is loaded
