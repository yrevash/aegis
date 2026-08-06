"""API package: the locked wire contracts and the HTTP/SSE router.

- :mod:`app.api.schemas` — the **locked** ``StreamEvent`` union and every
  endpoint request/response model (the single source of truth for the frontend).
- :mod:`app.api.routes` — the FastAPI ``router`` implementing those endpoints,
  mounted by :mod:`app.main`.

The router is intentionally **not** re-exported here: every module imports
``app.api.schemas``, so pulling ``routes`` (and its agent/data dependencies) into
this package's ``__init__`` would create an import cycle. Import the router from
:mod:`app.api.routes` directly.
"""

from __future__ import annotations
