"""Backend shim: the model role registry now lives in ``aegis``.

``ModelRole`` moved to :mod:`aegis.core.models` (shared, dependency-free) when
the retrieval module was extracted; the routing table + cost lookup
(``model_for``/``routing_table``/``is_small_model``) moved to
:mod:`aegis.gateway.routing` with the gateway extraction. This module
re-exports both **by identity** (not redefinition) so every existing import
site (``from app.core.models import ModelRole``) keeps working unchanged, and
a role passed through this module is the exact same enum the gateway routes on
— no risk of two ``ModelRole`` classes silently diverging.
"""

from __future__ import annotations

from aegis.core.models import ModelRole
from aegis.gateway.routing import is_small_model, model_for, routing_table

__all__ = ["ModelRole", "is_small_model", "model_for", "routing_table"]
