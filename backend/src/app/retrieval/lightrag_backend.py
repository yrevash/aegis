"""Backend shim: the LightRAG knowledge backend now lives in ``aegis.retrieval.lightrag_backend``.

``LightRAGBackend`` takes a duck-typed ``config`` (any object exposing
``vector_store_path``/``postgres_dsn``/``neo4j_uri``/``neo4j_user``/
``neo4j_password``) rather than the
platform's own ``Settings`` directly; production wiring (``pipeline.py``) passes a
``RetrievalConfig`` built from ``app.config.Settings``.
"""

from __future__ import annotations

from aegis.retrieval.lightrag_backend import LightRAGBackend

__all__ = ["LightRAGBackend"]
