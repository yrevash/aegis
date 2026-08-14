"""Backend shim for :mod:`aegis.forecast` — the platform's forecasting surface.

The forecasting itself is host-agnostic and lives in the importable ``aegis.forecast``
package. This package supplies only what a *host* can: where the series come from.

* :mod:`app.forecast.ledger` — per-tenant spend and call volume rolled out of
  ``usage_ledger``, the table the gateway already writes on every model call.
* :mod:`app.forecast.domain` — the client-facing demand series, read through the
  ``app.adapter`` seam so it retargets with everything else on swap day.
* :mod:`app.forecast.service` — composes those with the forecaster and the budget
  burn-down, off the event loop and memoised, for ``app.api.routes``.

Nothing heavy is imported here: :mod:`app.forecast.service` (and through it
statsforecast) is pulled in by the route handler, not at app startup.
"""

from __future__ import annotations

__all__ = ["domain", "ledger", "service"]
