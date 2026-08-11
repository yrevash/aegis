"""Backend shim: the audit-log writer now lives in ``aegis.governance``.

``record_audit`` / ``list_recent_audit`` — the first-class accountability trail — moved
to the standalone ``aegis.governance.audit`` (see ``/aegis``). Its session factory + RLS
binder are injected by the ``app.data.governance`` shim's ``configure_governance`` call
(both data-layer modules share one wiring), so importing that shim configures this one.

Re-exported here under the historical names so every existing call site (the agent graph,
tool executors, ``app.api.routes`` audit surface) is unchanged.
"""

from __future__ import annotations

from aegis.governance.audit import list_recent_audit, record_audit

# Importing the governance shim runs ``configure_governance(...)``, which wires the
# session factory + RLS binder into ``aegis.governance.audit`` as well as enforcement.
import app.data.governance  # noqa: F401 - import side-effect: configures the audit data layer

__all__ = ["list_recent_audit", "record_audit"]
