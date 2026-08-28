"""Trustworthy-ML spine — strangler shim over ``aegis.ml``.

The ensemble + MAPIE conformal + SHAP spine itself now lives in
:mod:`aegis.ml` (domain-agnostic, importable standalone). This module is the
thin backend-specific layer that wires the real domain spec
(``app.adapter.ml_spec``) into training and serving, so the module-level
contract other backend code depends on (``predict_explain``) is unchanged and
keeps predicting on genuine domain data.

Typical lifecycle::

    python -m app.ml                # offline, once: trains on the real domain frame
    from app.ml import predict_explain

    resp = predict_explain({"priority": "urgent", ...})
    resp.conformal_interval      # calibrated bounds (requested coverage)
    resp.conformal_confidence    # the requested coverage rate, e.g. 0.9
    resp.shap_attribution        # signed per-feature contributions

**There is no train-on-demand fallback, at either layer.** :func:`get_model`
resolves the in-process singleton, then a persisted artifact, and then *stops* —
mirroring :func:`aegis.ml.get_model`, whose "deliberately no third step" honesty fix
this shim used to defeat with a fallback of its own. That fallback ran
``train(_domain_spec(), None)``, and when ``app.adapter.ml_spec`` could not be
imported ``_domain_spec()`` returned ``None``, which resolves to
:data:`aegis.ml.FALLBACK_SPEC` and the built-in **noise synthesiser** — so
``/ml/explain`` and ``/ml/model-card``, the only surfaces that reach this module,
served a model fitted on random numbers as domain evidence, complete with a "90%
coverage" interval and ``feature_0…3`` drivers. Refusing is the honest answer:
train explicitly (``python -m app.ml``) and the endpoints serve; until then they
report that no model is available.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from aegis.ml import FALLBACK_SPEC, MLModelUnavailableError, TaskType
from aegis.ml.model import TrustworthyModel
from aegis.ml.spec import MLSpec, ResolvedSpec, resolve_spec

#: Where the HOST persists its domain-trained spine.
#:
#: Deliberately **not** ``aegis.ml.DEFAULT_ARTIFACT_PATH``. That path resolves
#: inside the installed ``aegis`` package directory, so re-exporting it here meant
#: the backend trained on the domain spec and wrote the result *into the library* —
#: the same file the library's own loader reads. Two consequences, both real:
#: a model fitted on one side would be picked up as the other's, and any
#: read-only or shared install (a wheel in site-packages) fails the write outright.
#:
#: A host artifact belongs to the host. This lives under the backend project root,
#: which is gitignored — the trained model is environment state, never source.
DEFAULT_ARTIFACT_PATH = (
    Path(__file__).resolve().parents[3] / ".artifacts" / "ml_spine.joblib"
)

if TYPE_CHECKING:
    import pandas as pd

    from app.api.schemas import MLExplainResponse
    from app.ml.spec import MLSpec as _MLSpec

__all__ = [
    "DEFAULT_ARTIFACT_PATH",
    "FALLBACK_SPEC",
    "MLModelUnavailableError",
    "MLSpec",
    "ResolvedSpec",
    "TaskType",
    "TrustworthyModel",
    "get_model",
    "load",
    "predict_explain",
    "resolve_spec",
    "train",
]

_MODEL: TrustworthyModel | None = None


def _domain_spec() -> MLSpec:
    """Return the domain adapter's ``ml_spec`` module.

    Returns:
        The ``app.adapter.ml_spec`` module object.

    Raises:
        MLModelUnavailableError: If the domain adapter cannot be imported. It used
            to return ``None`` here, which :func:`aegis.ml.spec.resolve_spec` reads
            as "no spec" and answers with :data:`FALLBACK_SPEC` — four random
            numeric features and a synthesised target. Training on that and serving
            it as this platform's model is exactly the substitution the ML honesty
            fix exists to refuse, so a missing adapter is now an error with a name,
            not a silent downgrade. Pass an explicit ``spec`` to :func:`train` to
            train on something other than the adapter.
    """
    try:
        from app.adapter import ml_spec
    except (ImportError, AttributeError) as exc:
        msg = (
            "The domain adapter (app.adapter.ml_spec) is not importable, so there is "
            "no domain spec to train on. Refusing to fall back to the built-in noise "
            "synthesiser and serve it as domain evidence — pass an explicit spec to "
            "app.ml.train(), or repair the adapter."
        )
        raise MLModelUnavailableError(msg) from exc
    return ml_spec


def train(
    spec: _MLSpec | None = None,
    frame: pd.DataFrame | None = None,
    *,
    confidence_level: float = 0.9,
    calibration_size: float = 0.25,
    random_state: int = 0,
    path: Path | str | None = DEFAULT_ARTIFACT_PATH,
) -> TrustworthyModel:
    """Train, calibrate and persist the spine on the real domain spec.

    Thin wrapper over :meth:`aegis.ml.model.TrustworthyModel.train` that injects
    ``app.adapter.ml_spec`` when no explicit spec is given, and updates the
    process-wide singleton returned by :func:`predict_explain`.

    Args:
        spec: Domain spec; resolved from ``app.adapter.ml_spec`` when ``None``
            (and an unimportable adapter is an error, never a quiet fallback to the
            noise synthesiser — see :func:`_domain_spec`).
        frame: Explicit training frame; the spec's own frame provider (or the
            built-in synthesiser) is used when ``None``.
        confidence_level: Guaranteed target coverage of the conformal predictor.
        calibration_size: Fraction of rows reserved for calibration.
        random_state: Seed for determinism.
        path: Where to persist the artifact; pass ``None`` to skip persistence.

    Returns:
        The freshly trained :class:`~aegis.ml.model.TrustworthyModel`.

    Raises:
        MLModelUnavailableError: If no ``spec`` is given and the domain adapter is
            not importable.
    """
    global _MODEL
    _MODEL = TrustworthyModel.train(
        spec if spec is not None else _domain_spec(),
        frame,
        confidence_level=confidence_level,
        calibration_size=calibration_size,
        random_state=random_state,
        path=path,
    )
    return _MODEL


#: The methods `/ml/explain` and `/ml/model-card` call on whatever this module
#: serves. A joblib file is not a contract -- it is a pickle of whatever the writer
#: happened to hold -- so this names what the routes actually require.
_SPINE_CONTRACT: tuple[str, ...] = ("model_card", "predict_explain")


def _refuse_foreign_artifact(obj: object, path: Path | str) -> None:
    """Refuse an artifact that is not a servable spine, naming what it is instead.

    **Why this exists, and why the check is on the object rather than its class.**
    ``DEFAULT_ARTIFACT_PATH`` is a shared address: a sibling project promotes
    challenger models onto exactly this file by design, and any tool that can write
    a ``.joblib`` can land here. ``TrustworthyModel.load`` is ``joblib.load`` with a
    return annotation, and an annotation is not a check -- so a foreign artifact
    loaded fine, sailed past a ``FileNotFoundError`` handler that could never see it,
    and failed at the first attribute access with ``AttributeError: 'Pipeline' object
    has no attribute 'model_card'`` and a 500. That message names a symptom four
    frames from the cause and says nothing about the file that caused it.

    An ``isinstance`` test would be the obvious check and is the wrong one: it makes
    the class the contract, so a wrapper that satisfies the routes perfectly is
    refused for having the wrong ancestor. The routes call two methods; that is the
    contract, and it is what gets tested.

    Args:
        obj: Whatever the artifact deserialised to.
        path: Where it came from, so the message names the file to fix.

    Raises:
        MLModelUnavailableError: When ``obj`` cannot serve. The routes already
            translate this to a 503 carrying the sentence, which is the honest answer
            -- there is no usable model -- rather than a 500 about an attribute.
    """
    missing = [name for name in _SPINE_CONTRACT if not callable(getattr(obj, name, None))]
    if not missing:
        return
    actual = f"{type(obj).__module__}.{type(obj).__qualname__}"
    raise MLModelUnavailableError(
        f"The artifact at {path} is a {actual}, which cannot serve this endpoint: it "
        f"has no {', '.join(missing)}. Aegis serves a TrustworthyModel -- the spine "
        "that carries the conformal interval and the SHAP attribution the response "
        "schema requires, neither of which a bare estimator can produce. Something "
        "overwrote this file with a different kind of model; a sibling project "
        "promotes onto this exact path. Retrain with `python -m app.ml`, or promote "
        "an artifact that satisfies the spine contract."
    )


def load(path: Path | str = DEFAULT_ARTIFACT_PATH) -> TrustworthyModel:
    """Load a persisted spine and cache it for serving.

    The loaded object is checked against the serving contract before it is cached,
    so a foreign artifact is refused here -- once, with the path in the message --
    rather than at the first attribute access inside a request handler.

    Args:
        path: Artifact produced by :func:`train`.

    Returns:
        The loaded :class:`~aegis.ml.model.TrustworthyModel`.

    Raises:
        MLModelUnavailableError: When the file holds something that cannot serve.
    """
    global _MODEL
    candidate = TrustworthyModel.load(path)
    _refuse_foreign_artifact(candidate, path)
    _MODEL = candidate
    return _MODEL


def get_model() -> TrustworthyModel:
    """Return the cached spine, loading a persisted artifact on first use.

    Resolution order: the in-process singleton, then a persisted artifact at
    :data:`DEFAULT_ARTIFACT_PATH`. There is deliberately **no third step**, for the
    same reason :func:`aegis.ml.get_model` has none: the removed step trained a model
    on demand, inside whatever request happened to be first, and — whenever the domain
    adapter was unimportable — trained it on the built-in noise synthesiser and served
    its point prediction, its "90% coverage" interval and its ``feature_0…3`` drivers
    as if they were evidence about this domain. A caller had no way to tell that apart
    from a real model.

    Refusing is the honest answer. The agent's ML node is best-effort and simply omits
    the evidence; ``/ml/explain`` and ``/ml/model-card`` answer 503 with the command
    that fixes it. To serve, train the artifact explicitly — ``python -m app.ml`` — or
    assign a model with :func:`load` / :func:`train`.

    Returns:
        A ready-to-serve :class:`~aegis.ml.model.TrustworthyModel`.

    Raises:
        MLModelUnavailableError: If no model is cached and no artifact exists.
    """
    global _MODEL
    if _MODEL is not None:
        return _MODEL
    try:
        return load(DEFAULT_ARTIFACT_PATH)
    except FileNotFoundError as exc:
        msg = (
            f"No trained ML artifact at {DEFAULT_ARTIFACT_PATH}. The spine will not "
            "silently fall back to a model fitted on synthetic noise and serve it as "
            "domain evidence — train one on the real domain frame first: "
            "`python -m app.ml`."
        )
        raise MLModelUnavailableError(msg) from exc


def predict_explain(features: dict[str, Any]) -> MLExplainResponse:
    """Predict, conformalise and explain one input (module-level contract).

    Args:
        features: ``feature name → value`` for a single prediction.

    Returns:
        An :class:`~app.api.schemas.MLExplainResponse` with the prediction, the
        calibrated conformal interval, the requested coverage rate and the
        signed SHAP attributions.

    Raises:
        MLModelUnavailableError: If no trained model is available to serve.
    """
    return get_model().predict_explain(features)
