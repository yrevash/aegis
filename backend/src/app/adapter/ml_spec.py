"""ML spec — features + a genuinely predictable target for the ML spine.

This is **piece 2 of 10** of the adapter. It declares what :mod:`app.ml` trains on:

* :data:`FEATURES` — the typed feature contract (name, dtype, description).
* :data:`TARGET` — a **regression** target, ``resolution_hours``.
* :func:`features_for_request` — turn a schema record (joined to its agent and
  customer) into a flat feature dict.
* :func:`feature_matrix` — build ``(X, y)`` from a :class:`SyntheticDataset`.
* :func:`latent_resolution_hours` — the **ground-truth generative signal**.

The last function is the linchpin of the whole adapter's "predictability" claim.
It is a documented, monotone latent function of the features. The generator
(:mod:`app.adapter.generator`) calls it to *set* each resolved request's
``resolution_hours`` (plus seeded noise), so the label is a real function of the
features by construction — an XGBoost/MAPIE model can therefore learn it and the
conformal intervals are meaningful rather than fitting noise. Keeping the signal
here (not in the generator) makes ml_spec the single source of truth: change the
target's drivers in one place and the data stays consistent.

No heavyweight ML libraries are imported here on purpose — this module is pure
Python so it loads (and tests) without numpy/pandas/xgboost present. The core
``app.ml`` module owns the encoding + model fitting.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    import pandas as pd

    from app.api.schemas import MLExplainResponse

from app.adapter.schema import (
    Category,
    Channel,
    Customer,
    CustomerTier,
    Priority,
    Region,
    ServiceRequest,
    SupportAgent,
    SyntheticDataset,
)

FeatureDType = Literal["categorical", "numeric", "boolean"]


class FeatureSpec(BaseModel):
    """One model feature: its name, dtype and a human description."""

    name: str
    dtype: FeatureDType
    description: str
    levels: list[str] | None = Field(
        default=None, description="Allowed values for categorical features."
    )


class TargetSpec(BaseModel):
    """The prediction target the ML spine learns."""

    name: str
    task: Literal["regression", "classification"]
    unit: str | None = None
    description: str


# ─────────────────────────────────────────────────────────────────────────────
# The contract
# ─────────────────────────────────────────────────────────────────────────────

FEATURES: list[FeatureSpec] = [
    FeatureSpec(
        name="priority",
        dtype="categorical",
        description="Request priority; higher priority is worked faster.",
        levels=[p.value for p in Priority],
    ),
    FeatureSpec(
        name="category",
        dtype="categorical",
        description="Subject area; technical cases take longest.",
        levels=[c.value for c in Category],
    ),
    FeatureSpec(
        name="channel",
        dtype="categorical",
        description="Intake channel; async channels (email) resolve slower.",
        levels=[c.value for c in Channel],
    ),
    FeatureSpec(
        name="region",
        dtype="categorical",
        description="Geographic region; affects staffing windows.",
        levels=[r.value for r in Region],
    ),
    FeatureSpec(
        name="customer_tier",
        dtype="categorical",
        description="Commercial tier; enterprise customers get faster handling.",
        levels=[t.value for t in CustomerTier],
    ),
    FeatureSpec(
        name="agent_tenure_months",
        dtype="numeric",
        description="Experience of the assigned agent; experienced agents are faster.",
    ),
    FeatureSpec(
        name="queue_depth_at_open",
        dtype="numeric",
        description="Backlog when opened; more backlog means slower resolution.",
    ),
    FeatureSpec(
        name="reopened_count",
        dtype="numeric",
        description="Times reopened; reopens add rework time.",
    ),
    FeatureSpec(
        name="description_length",
        dtype="numeric",
        description="Characters in the description; a weak complexity proxy.",
    ),
]
"""The ordered, typed feature contract the ML spine consumes."""

FEATURE_NAMES: list[str] = [f.name for f in FEATURES]
CATEGORICAL_FEATURES: list[str] = [f.name for f in FEATURES if f.dtype == "categorical"]
NUMERIC_FEATURES: list[str] = [f.name for f in FEATURES if f.dtype != "categorical"]

TARGET: TargetSpec = TargetSpec(
    name="resolution_hours",
    task="regression",
    unit="hours",
    description="Wall-clock hours from open to resolved; a smooth function of the "
    "operational features, so it is genuinely learnable with a calibrated interval.",
)


# ─────────────────────────────────────────────────────────────────────────────
# Ground-truth latent signal (shared by the generator)
# ─────────────────────────────────────────────────────────────────────────────

_INTERCEPT: float = 12.0

_PRIORITY_SPEEDUP: dict[str, float] = {
    Priority.LOW.value: 0.0,
    Priority.MEDIUM.value: 4.0,
    Priority.HIGH.value: 8.0,
    Priority.URGENT.value: 12.0,
}

_CATEGORY_BASE: dict[str, float] = {
    Category.BILLING.value: 10.0,
    Category.TECHNICAL.value: 30.0,
    Category.ACCOUNT.value: 14.0,
    Category.SHIPPING.value: 20.0,
    Category.GENERAL.value: 8.0,
}

_CHANNEL_DELAY: dict[str, float] = {
    Channel.PHONE.value: 2.0,
    Channel.CHAT.value: 1.0,
    Channel.EMAIL.value: 8.0,
    Channel.PORTAL.value: 6.0,
}

_REGION_DELAY: dict[str, float] = {
    Region.NA.value: 2.0,
    Region.EU.value: 4.0,
    Region.APAC.value: 6.0,
    Region.LATAM.value: 7.0,
}

_TIER_SPEEDUP: dict[str, float] = {
    CustomerTier.STANDARD.value: 0.0,
    CustomerTier.PREMIUM.value: 3.0,
    CustomerTier.ENTERPRISE.value: 6.0,
}

_FLOOR_HOURS: float = 0.5


def latent_resolution_hours(features: dict) -> float:
    """Compute the noise-free ground-truth resolution time for a feature row.

    This is the deterministic latent function the generator samples around. It is
    monotone in every driver (higher priority → faster; deeper queue → slower;
    more experienced agent → faster), which is exactly what makes the target
    predictable for a tree model and gives calibrated conformal intervals meaning.

    Args:
        features: A feature dict as produced by :func:`features_for_request`.
            Missing keys fall back to neutral values so partial rows still score.

    Returns:
        The expected resolution time in hours, floored at a small positive value.
    """
    hours = _INTERCEPT
    hours += _CATEGORY_BASE.get(features.get("category", ""), 12.0)
    hours += _CHANNEL_DELAY.get(features.get("channel", ""), 4.0)
    hours += _REGION_DELAY.get(features.get("region", ""), 4.0)
    hours -= _PRIORITY_SPEEDUP.get(features.get("priority", ""), 0.0)
    hours -= _TIER_SPEEDUP.get(features.get("customer_tier", ""), 0.0)

    hours += 0.8 * float(features.get("queue_depth_at_open", 0) or 0)
    hours += 6.0 * float(features.get("reopened_count", 0) or 0)
    hours -= 0.5 * float(features.get("agent_tenure_months", 0) or 0)
    hours += 0.01 * float(features.get("description_length", 0) or 0)

    return max(_FLOOR_HOURS, round(hours, 3))


# ─────────────────────────────────────────────────────────────────────────────
# Record → features
# ─────────────────────────────────────────────────────────────────────────────


def features_for_request(
    request: ServiceRequest,
    *,
    agent: SupportAgent | None,
    customer: Customer | None,
) -> dict:
    """Extract the model feature dict for one request.

    Args:
        request: The service request to featurise.
        agent: The assigned agent (joined), or None if unassigned.
        customer: The owning customer (joined), or None if unknown.

    Returns:
        A flat ``{feature_name: value}`` dict covering every entry in
        :data:`FEATURES`. Categorical values are the enum ``.value`` strings.
    """
    return {
        "priority": request.priority.value,
        "category": request.category.value,
        "channel": request.channel.value,
        "region": request.region.value,
        "customer_tier": customer.tier.value if customer else CustomerTier.STANDARD.value,
        "agent_tenure_months": agent.tenure_months if agent else 0,
        "queue_depth_at_open": request.queue_depth_at_open,
        "reopened_count": request.reopened_count,
        "description_length": len(request.description),
    }


def describe_prediction(resp: MLExplainResponse, *, top_k: int = 3) -> str:
    """Render an ML prediction as decision-support text for the agent's reasoning.

    This is the **domain** framing of the spine's output: it names the target
    (:data:`TARGET`) and its unit, states the calibrated interval / confidence, and
    lists the strongest signed SHAP drivers. The core injects the returned block
    into the planner and generate prompts so the agent plans *with* the model
    (predict-then-act) and can explain itself from the model's actual drivers.

    Args:
        resp: The spine response (prediction, conformal fields, SHAP attribution).
        top_k: How many top SHAP drivers to surface.

    Returns:
        A compact, human-readable multi-line summary safe to embed in a prompt.
    """
    unit = f" {TARGET.unit}" if TARGET.unit else ""
    if isinstance(resp.prediction, (int, float)):
        head = f"Predicted {TARGET.name}: {float(resp.prediction):.1f}{unit}"
    else:
        head = f"Predicted {TARGET.name}: {resp.prediction}"

    lines = [f"ML decision-support ({TARGET.task}):", f"- {head}"]
    if resp.conformal_interval is not None and resp.conformal_confidence is not None:
        low, high = resp.conformal_interval
        lines.append(
            f"- {resp.conformal_confidence:.0%} confidence interval "
            f"[{low:.1f}, {high:.1f}]{unit}"
        )
    elif resp.prediction_set_size is not None and resp.conformal_confidence is not None:
        lines.append(
            f"- {resp.conformal_confidence:.0%} conformal set size "
            f"{resp.prediction_set_size} (1 = confident)"
        )
    drivers = [
        f"{f.feature} ({'+' if f.contribution >= 0 else '−'}{abs(f.contribution):.2f})"
        for f in resp.shap_attribution[:top_k]
    ]
    if drivers:
        lines.append("- Top drivers (SHAP): " + ", ".join(drivers))
    lines.append("Use this prediction to prioritise and choose the right action.")
    return "\n".join(lines)


def feature_matrix(dataset: SyntheticDataset) -> tuple[list[dict], list[float]]:
    """Build the training matrix ``(X, y)`` from a dataset's labelled requests.

    Only requests with a measured resolution (see
    :meth:`SyntheticDataset.labelled_requests`) contribute rows, joining each to
    its agent and customer for the full feature set.

    Args:
        dataset: The synthetic world to draw rows from.

    Returns:
        A tuple ``(X, y)`` where ``X`` is a list of feature dicts and ``y`` is the
        list of ``resolution_hours`` targets, aligned by index.
    """
    features: list[dict] = []
    targets: list[float] = []
    for request in dataset.labelled_requests():
        agent = (
            dataset.agent_by_id(request.assigned_agent_id)
            if request.assigned_agent_id
            else None
        )
        customer = dataset.customer_by_id(request.customer_id)
        features.append(features_for_request(request, agent=agent, customer=customer))
        # labelled_requests() guarantees resolution_hours is not None.
        targets.append(float(request.resolution_hours or 0.0))
    return features, targets


def training_frame(*, num_requests: int = 1200, seed: int = 7) -> pd.DataFrame:
    """Build the ML spine's labelled training frame from a fresh synthetic world.

    This is the ``frame_provider`` the spine resolves at (offline) train time: it
    generates a deterministic synthetic dataset **synchronously** (no LLM, no
    network) and turns its labelled requests into a ``DataFrame`` with one column
    per :data:`FEATURE_NAMES` plus the :data:`TARGET` column. Because the label is
    the real :func:`latent_resolution_hours` function of the features, the model
    trains on genuine domain signal — never generic noise.

    Args:
        num_requests: Requests to synthesise (more ⇒ more labelled training rows).
        seed: Seed for the synthetic world (deterministic frame under a fixed seed).

    Returns:
        A labelled training frame; feature columns keep their native dtypes
        (categorical values are enum ``.value`` strings, numerics are numbers).
    """
    import pandas as pd

    from app.adapter.generator import GeneratorConfig, generate_synthetic_sync

    dataset = generate_synthetic_sync(
        GeneratorConfig(seed=seed, num_requests=num_requests)
    )
    rows, targets = feature_matrix(dataset)
    frame = pd.DataFrame(rows, columns=FEATURE_NAMES)
    frame[TARGET.name] = targets
    return frame
