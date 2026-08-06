"""Schema validation tests."""

from __future__ import annotations

from datetime import datetime

import pytest
from pydantic import ValidationError

from app.adapter.schema import (
    Category,
    Channel,
    Customer,
    CustomerTier,
    Priority,
    Region,
    RequestStatus,
    ServiceRequest,
)


def _request(**overrides) -> ServiceRequest:
    base = {
        "id": "req-000001",
        "title": "t",
        "description": "d",
        "category": Category.BILLING,
        "priority": Priority.HIGH,
        "channel": Channel.EMAIL,
        "region": Region.NA,
        "status": RequestStatus.RESOLVED,
        "customer_id": "cust-0001",
        "created_at": datetime(2024, 1, 1),
        "updated_at": datetime(2024, 1, 2),
        "queue_depth_at_open": 3,
        "sla_hours": 24.0,
        "resolution_hours": 10.0,
    }
    base.update(overrides)
    return ServiceRequest(**base)


def test_resolved_request_is_labelled():
    assert _request().is_resolved is True


def test_open_request_is_not_labelled():
    req = _request(status=RequestStatus.NEW, resolution_hours=None)
    assert req.is_resolved is False


def test_negative_queue_depth_rejected():
    with pytest.raises(ValidationError):
        _request(queue_depth_at_open=-1)


def test_satisfaction_bounds_enforced():
    with pytest.raises(ValidationError):
        _request(satisfaction_score=9)


def test_customer_roundtrips():
    cust = Customer(
        id="cust-0001",
        name="Acme Ltd",
        email="a@acme.example",
        region=Region.EU,
        tier=CustomerTier.ENTERPRISE,
        created_at=datetime(2023, 1, 1),
    )
    assert Customer.model_validate(cust.model_dump()) == cust
