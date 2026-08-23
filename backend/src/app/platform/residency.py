"""Data residency — every destination this deployment can reach, derived from its own wiring.

India's DPDP Act s.16 governs transfer of personal data outside India, and the CERT-In
Directions of April 2022 require ICT logs to be maintained *within Indian jurisdiction*.
Both questions have the same precondition and it is not a legal one: **where does the
data actually go?** A hand-written answer to that is worth nothing — it is true on the
day it is typed and false the first time somebody edits an environment variable.

So this module does not assert residency. It **derives** it:

* Every field on :class:`~app.config.Settings` that names a network destination is
  declared here exactly once, with the sentence saying what travels through it.
* At read time the live value is parsed, the credentials are stripped, the host is
  classified as loopback / private / external, and *that* is what the surface reports.
* ``backend/tests/api/test_residency.py`` walks ``Settings.model_fields`` and fails if a
  destination-bearing field exists that no channel claims. A new outbound dependency
  cannot be added without either declaring it here or breaking the suite.

The consequence is the property that makes the claim usable: **pointing any of these at
a host outside India flips the surface to ``external`` on the next read.** The report
cannot be more optimistic than the configuration, which is the only version of a
residency claim a reviewer should accept.

**What it can say and what it cannot.** It reports where a configured destination *is
addressed*, which is a fact about this deployment. It does not geolocate an IP, it does
not know where a cloud region physically sits, and it does not inspect a payload. A
destination reported ``external`` is definitely off-host; a destination reported
``local`` is on the machine or the private network the deployment runs on, and where
that machine sits is the operator's fact, not this module's.

Nothing here dials anything. Parsing a URL string is the whole cost.
"""

from __future__ import annotations

import ipaddress
from datetime import UTC, datetime
from enum import StrEnum
from urllib.parse import urlsplit

from pydantic import BaseModel, Field

from app.config import Settings, get_settings

__all__ = [
    "ChannelRole",
    "EgressChannel",
    "Locality",
    "ResidencyReport",
    "build_residency",
    "destination_fields",
]


class Locality(StrEnum):
    """Where a configured destination sits relative to this deployment."""

    #: Loopback, a private range, or a ``.local`` name — the same host or the same LAN.
    LOCAL = "local"
    #: A publicly routable host. Whatever this channel carries leaves the deployment.
    EXTERNAL = "external"
    #: Not configured. Nothing travels this channel at all in this deployment.
    DISABLED = "disabled"
    #: Configured but unparseable. Never reported as local — an unreadable destination
    #: is an unknown destination, and guessing in the reassuring direction is the
    #: failure mode this whole surface exists to refuse.
    UNKNOWN = "unknown"


class ChannelRole(StrEnum):
    """What the destination does with what reaches it — the half that decides residency."""

    #: It **stores** data. Where this points is where tenant data lives at rest.
    STORE = "store"
    #: It **processes** data in transit and (as far as this deployment knows) keeps none.
    PROCESS = "process"
    #: Aegis's own listening address. Nothing leaves through it.
    SELF = "self"


class EgressChannel(BaseModel):
    """One destination this deployment can reach, and what reaches it."""

    id: str = Field(description="Stable slug for the channel.")
    name: str = Field(description="Human name for the destination.")
    role: ChannelRole = Field(description="store / process / self.")
    locality: Locality = Field(description="local / external / disabled / unknown.")
    destination: str = Field(
        default="",
        description="Scheme and host:port as configured, credentials stripped. Empty when unset.",
    )
    setting: str = Field(description="The Settings field or environment variable that decides it.")
    carries: str = Field(description="What actually travels this channel. One sentence.")
    code_ref: str = Field(description="The repository path where the dial happens.")


class ResidencyReport(BaseModel):
    """Every destination, its locality, and the counts a reader wants first."""

    generated_at: str = Field(description="ISO-8601 UTC timestamp of this read.")
    channels: list[EgressChannel] = Field(description="One entry per configured destination.")
    stores_local: int = Field(description="Data-at-rest destinations resolving to this host/LAN.")
    stores_external: int = Field(description="Data-at-rest destinations resolving off-host.")
    external: int = Field(description="Channels of any role that leave the deployment.")
    local: int = Field(description="Channels of any role that stay on this host or LAN.")
    disabled: int = Field(description="Declared channels this deployment has not configured.")
    note: str = Field(description="What this report can and cannot establish.")


NOTE = (
    "Derived from live configuration on every read, not asserted: each destination below is "
    "parsed from the setting named beside it, so re-pointing one at a host outside this "
    "deployment changes this table on the next read. It reports where a destination is "
    "addressed — it does not geolocate an IP, resolve a cloud region to a country, or inspect "
    "a payload."
)


# ─────────────────────────────────────────────────────────────────────────────
# The declared channels
#
# One row per destination-bearing setting. The test module walks Settings.model_fields
# and fails on any destination field no row claims, so this table cannot silently fall
# behind the configuration it describes.
# ─────────────────────────────────────────────────────────────────────────────

#: The suffixes that make a settings field a network destination. Used here and asserted
#: against ``Settings.model_fields`` by the test module.
DESTINATION_SUFFIXES: tuple[str, ...] = (
    "_url",
    "_uri",
    "_dsn",
    "_address",
    "_servers",
)

_DECLARED: tuple[tuple[str, str, str, ChannelRole, str, str], ...] = (
    # (setting, id, name, role, carries, code_ref)
    (
        "postgres_dsn",
        "postgres",
        "PostgreSQL — the system of record",
        ChannelRole.STORE,
        "Every tenant row at rest: documents, chunks, memory facts and raw turns, approvals, "
        "the usage ledger and the audit trail.",
        "backend/src/app/data/session.py",
    ),
    (
        "postgres_admin_dsn",
        "postgres-admin",
        "PostgreSQL — the migration/bootstrap connection",
        ChannelRole.STORE,
        "Schema and role provisioning against the same database. No request traffic.",
        "aegis/src/aegis/governance/rls.py",
    ),
    (
        "db_console_dsn",
        "db-console",
        "PostgreSQL — the read-only console role",
        ChannelRole.STORE,
        "A closed set of parameterised SELECTs over a role that holds SELECT and nothing else.",
        "backend/src/app/api/routes_db.py",
    ),
    (
        "qdrant_url",
        "qdrant",
        "Qdrant — the dense index",
        ChannelRole.STORE,
        "Chunk embeddings and their payloads, mirrored from the embedding of record in Postgres.",
        "aegis/src/aegis/retrieval/vector_store.py",
    ),
    (
        "neo4j_uri",
        "neo4j",
        "Neo4j — the knowledge graph",
        ChannelRole.STORE,
        "Entities and relations extracted from tenant documents, and the graph queries over them.",
        "backend/src/app/ingestion/graph_projection.py",
    ),
    (
        "redis_url",
        "redis",
        "Redis — caches and queues",
        ChannelRole.STORE,
        "Retrieval and answer caches (query text, embeddings, cached answers), web-search results "
        "and rate-limit counters.",
        "aegis/src/aegis/retrieval/cache.py",
    ),
    (
        "genailab_base_url",
        "model-gateway",
        "Model gateway — the single LLM chokepoint",
        ChannelRole.PROCESS,
        "The one channel that carries tenant content off the host: prompts (post-redaction), "
        "completions, and the chunk text sent for embedding. Every model call in the platform "
        "goes through here and nowhere else.",
        "aegis/src/aegis/gateway/llm.py",
    ),
    (
        "temporal_address",
        "temporal",
        "Temporal — durable workflow orchestration",
        ChannelRole.PROCESS,
        "Workflow and activity arguments for ingestion and reindex jobs, including document ids.",
        "backend/src/app/config.py",
    ),
    (
        "superset_base_url",
        "superset",
        "Superset — the analytics instance",
        ChannelRole.PROCESS,
        "Guest-token mints carrying a tenant row-level-security clause, and the dashboard the "
        "browser then embeds.",
        "backend/src/app/api/routes_analytics.py",
    ),
    (
        "mcp_client_servers",
        "mcp-peers",
        "MCP peers — declared external tool servers",
        ChannelRole.PROCESS,
        "Tool names and arguments an agent chose, to whichever peer a platform admin declared. "
        "Every peer tool is HIGH risk — the human gate — until an admin lowers a named one.",
        "backend/src/app/mcp/client.py",
    ),
    (
        "mcp_server_url",
        "mcp-self",
        "Aegis's own MCP endpoint",
        ChannelRole.SELF,
        "Where Aegis serves its own MCP surface. An address it publishes, not one it dials.",
        "backend/src/app/mcp/server.py",
    ),
    (
        "mcp_issuer_url",
        "mcp-issuer",
        "Aegis's own MCP OAuth issuer identity",
        ChannelRole.SELF,
        "The issuer string Aegis stamps on its own MCP tokens. An identifier, not a destination "
        "anything is sent to.",
        "backend/src/app/mcp/server.py",
    ),
)

#: Channels with no destination setting of their own — the host is fixed by the library
#: or the provider, and a boolean/credential decides whether they run at all. Declared
#: explicitly so the inventory is not quietly narrower than the code.
_FIXED: tuple[tuple[str, str, str, ChannelRole, str, str, str], ...] = (
    # (id, name, host, role, carries, setting, code_ref)
    (
        "web-search",
        "Tavily — the web-search provider",
        "api.tavily.com",
        ChannelRole.PROCESS,
        "The search query text a research step composed. No document, memory or tenant row is "
        "sent; with no key configured the run degrades to internal evidence and says so.",
        "TAVILY_API_KEY",
        "aegis/src/aegis/websearch/tavily.py",
    ),
    (
        "pypi",
        "PyPI — the dependency freshness check",
        "pypi.org",
        ChannelRole.PROCESS,
        "Package names already present in the lockfiles, on an explicit operator action. No "
        "tenant data of any kind.",
        "POST /stack/patch-check (operator-initiated)",
        "backend/src/app/platform/patches.py",
    ),
    (
        "model-weights",
        "Hugging Face — local reranker weights",
        "huggingface.co",
        ChannelRole.PROCESS,
        "A one-way ONNX cross-encoder download on first load, cached on disk thereafter. Nothing "
        "is uploaded; the rerank itself then runs on this host.",
        "rerank_local",
        "aegis/src/aegis/retrieval/local_reranker.py",
    ),
)


# ─────────────────────────────────────────────────────────────────────────────
# Derivation
# ─────────────────────────────────────────────────────────────────────────────


def destination_fields(settings_cls: type[Settings] = Settings) -> set[str]:
    """Return every ``Settings`` field name that carries a network destination.

    Args:
        settings_cls: The settings model to inspect. Parameterised so the test module
            reads the same rule this module does rather than a copy of it.

    Returns:
        The field names ending in one of :data:`DESTINATION_SUFFIXES`.
    """
    return {
        name for name in settings_cls.model_fields if name.endswith(DESTINATION_SUFFIXES)
    }


def _host_locality(host: str) -> Locality:
    """Classify one hostname or IP literal as local or external.

    Loopback, link-local, private ranges and ``.local``/``localhost`` names are local;
    anything publicly routable is external. An empty host is *unknown*, never local.
    """
    name = host.strip().strip("[]").lower()
    if not name:
        return Locality.UNKNOWN
    if name in {"localhost", "localhost.localdomain"} or name.endswith((".local", ".localhost")):
        return Locality.LOCAL
    try:
        address = ipaddress.ip_address(name)
    except ValueError:
        return Locality.EXTERNAL
    if address.is_loopback or address.is_private or address.is_link_local or address.is_unspecified:
        return Locality.LOCAL
    return Locality.EXTERNAL


def _split(raw: str) -> tuple[str, Locality]:
    """Parse one configured destination into a credential-free label and a locality.

    Accepts a full URL, a bare ``host:port`` (Temporal's address shape) and a Postgres
    DSN. Credentials are dropped before anything is returned: this payload is read by a
    security reviewer, not by a connection pool.
    """
    value = raw.strip()
    if not value:
        return "", Locality.DISABLED
    if "://" not in value and value.startswith("/"):
        # A relative path is same-origin by construction — it names a mount point on
        # this deployment, not a host. Reporting it "unknown" would raise an alarm
        # about the one shape that cannot possibly leave.
        return f"{value} (same origin)", Locality.LOCAL
    candidate = value if "://" in value else f"//{value}"
    try:
        parts = urlsplit(candidate)
        host = parts.hostname or ""
        port = parts.port
    except ValueError:
        return "unparseable", Locality.UNKNOWN
    if not host:
        return "unparseable", Locality.UNKNOWN
    scheme = f"{parts.scheme}://" if parts.scheme else ""
    label = f"{scheme}{host}" + (f":{port}" if port else "")
    return label, _host_locality(host)


def _peer_channel(raw: str) -> tuple[str, Locality]:
    """Collapse the ``id=url,id=url`` MCP peer list into one destination and one locality.

    The **worst** locality wins: one externally-hosted peer among five local ones means
    this deployment reaches outside, and a report that averaged that away would be
    telling a reviewer the opposite of the thing they asked.
    """
    urls = [
        chunk.partition("=")[2].strip()
        for chunk in raw.split(",")
        if chunk.strip() and "=" in chunk
    ]
    if not urls:
        return "", Locality.DISABLED
    parsed = [_split(url) for url in urls]
    labels = ", ".join(label for label, _ in parsed if label)
    order = (Locality.EXTERNAL, Locality.UNKNOWN, Locality.LOCAL, Locality.DISABLED)
    worst = next(
        (band for band in order if any(loc is band for _, loc in parsed)),
        Locality.UNKNOWN,
    )
    return labels, worst


def build_residency(settings: Settings | None = None) -> ResidencyReport:
    """Return the destination inventory for the running configuration.

    Args:
        settings: The settings to read. Defaults to the process's live settings; passed
            explicitly by tests that need to prove the verdict follows the wiring.

    Returns:
        A :class:`ResidencyReport` with one channel per declared destination, each
        carrying the credential-free address, the locality derived from it, and the
        sentence saying what travels there.
    """
    live = settings if settings is not None else get_settings()
    channels: list[EgressChannel] = []

    for setting, cid, name, role, carries, code_ref in _DECLARED:
        raw = str(getattr(live, setting, "") or "")
        if cid == "mcp-peers":
            destination, locality = _peer_channel(raw)
        else:
            destination, locality = _split(raw)
        channels.append(
            EgressChannel(
                id=cid,
                name=name,
                role=role,
                locality=locality,
                destination=destination,
                setting=setting,
                carries=carries,
                code_ref=code_ref,
            )
        )

    enabled = {
        "web-search": bool(str(getattr(live, "tavily_api_key", "") or "").strip()),
        "pypi": True,
        "model-weights": bool(getattr(live, "rerank_local", False)),
    }
    for cid, name, host, role, carries, setting, code_ref in _FIXED:
        channels.append(
            EgressChannel(
                id=cid,
                name=name,
                role=role,
                locality=Locality.EXTERNAL if enabled[cid] else Locality.DISABLED,
                destination=host if enabled[cid] else "",
                setting=setting,
                carries=carries,
                code_ref=code_ref,
            )
        )

    stores = [c for c in channels if c.role is ChannelRole.STORE]
    return ResidencyReport(
        generated_at=datetime.now(UTC).isoformat(),
        channels=channels,
        stores_local=sum(1 for c in stores if c.locality is Locality.LOCAL),
        stores_external=sum(1 for c in stores if c.locality is Locality.EXTERNAL),
        external=sum(1 for c in channels if c.locality is Locality.EXTERNAL),
        local=sum(1 for c in channels if c.locality is Locality.LOCAL),
        disabled=sum(1 for c in channels if c.locality is Locality.DISABLED),
        note=NOTE,
    )
