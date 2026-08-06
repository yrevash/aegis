"""ML-spec tests — feature contract, latent signal and matrix building."""

from __future__ import annotations

from app.adapter.generator import GeneratorConfig, generate_synthetic
from app.adapter.ml_spec import (
    FEATURE_NAMES,
    feature_matrix,
    features_for_request,
    latent_resolution_hours,
)


def _base_features() -> dict:
    return {
        "priority": "low",
        "category": "billing",
        "channel": "chat",
        "region": "na",
        "customer_tier": "standard",
        "agent_tenure_months": 0,
        "queue_depth_at_open": 0,
        "reopened_count": 0,
        "description_length": 0,
    }


def test_latent_monotone_in_priority():
    low = _base_features()
    urgent = {**low, "priority": "urgent"}
    assert latent_resolution_hours(urgent) < latent_resolution_hours(low)


def test_latent_monotone_in_queue_depth():
    shallow = _base_features()
    deep = {**shallow, "queue_depth_at_open": 40}
    assert latent_resolution_hours(deep) > latent_resolution_hours(shallow)


def test_latent_monotone_in_experience():
    junior = _base_features()
    senior = {**junior, "agent_tenure_months": 60}
    assert latent_resolution_hours(senior) < latent_resolution_hours(junior)


def test_latent_floored_positive():
    fast = {
        **_base_features(),
        "priority": "urgent",
        "category": "general",
        "channel": "chat",
        "region": "na",
        "customer_tier": "enterprise",
        "agent_tenure_months": 60,
    }
    assert latent_resolution_hours(fast) >= 0.5


async def test_feature_matrix_aligned_and_covers_contract():
    cfg = GeneratorConfig(num_requests=50, seed=9, use_llm=False)
    dataset = await generate_synthetic(cfg)
    x, y = feature_matrix(dataset)

    assert len(x) == len(y) == len(dataset.labelled_requests())
    assert x, "there should be at least one labelled row"
    assert set(x[0].keys()) == set(FEATURE_NAMES)
    assert all(isinstance(v, float) for v in y)


def test_features_for_request_from_dataset_keys():
    # features_for_request tolerates missing joins (agent/customer None).
    from datetime import datetime

    from app.adapter.schema import (
        Category,
        Channel,
        Priority,
        Region,
        RequestStatus,
        ServiceRequest,
    )

    req = ServiceRequest(
        id="req-x",
        title="t",
        description="hello world",
        category=Category.SHIPPING,
        priority=Priority.MEDIUM,
        channel=Channel.EMAIL,
        region=Region.APAC,
        status=RequestStatus.NEW,
        customer_id="cust-x",
        created_at=datetime(2024, 1, 1),
        updated_at=datetime(2024, 1, 1),
        queue_depth_at_open=2,
        sla_hours=48.0,
    )
    feats = features_for_request(req, agent=None, customer=None)
    assert set(feats.keys()) == set(FEATURE_NAMES)
    assert feats["description_length"] == len("hello world")
