"""Aegis core — the dependency-free module contract.

Holds the shared interfaces, data types, registry, config, health probes and the
lazy-import helper every Aegis component depends on. This package imports nothing
internal and pulls in no heavy dependency, so any component that depends only on
it stays cheap to install.
"""

from __future__ import annotations
