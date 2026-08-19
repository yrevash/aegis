"""Aegis core — the dependency-free module contract.

Holds the shared interfaces, data types, registry, config, health probes and the
lazy-import helper every Aegis component depends on. This package imports nothing
internal and pulls in no heavy dependency, so any component that depends only on
it stays cheap to install.
"""

from __future__ import annotations

from aegis.core.config import AegisMode, CoreSettings
from aegis.core.deprecation import (
    AegisDeprecationWarning,
    deprecated,
    warn_deprecated,
)
from aegis.core.events import (
    AegisEvent,
    GuardrailEvent,
    SpanKind,
    StepFinished,
    StepStarted,
)
from aegis.core.interfaces import ChatCompleter, Guardrail
from aegis.core.lazy import require
from aegis.core.registry import available, get, register
from aegis.core.types import (
    ApprovalDecision,
    FormatCheck,
    GuardResult,
    GuardStage,
    GuardVerdict,
    InjectionVerdict,
    PIIMatch,
    RiskLevel,
    RunStatus,
)

__all__ = [
    "AegisDeprecationWarning",
    "AegisEvent",
    "AegisMode",
    "ApprovalDecision",
    "ChatCompleter",
    "CoreSettings",
    "FormatCheck",
    "Guardrail",
    "GuardResult",
    "GuardStage",
    "GuardVerdict",
    "GuardrailEvent",
    "InjectionVerdict",
    "PIIMatch",
    "RiskLevel",
    "RunStatus",
    "SpanKind",
    "StepFinished",
    "StepStarted",
    "available",
    "deprecated",
    "get",
    "register",
    "require",
    "warn_deprecated",
]
