"""The one supported way up: :class:`Aegis`.

    aegis = await Aegis.from_env(adapter="myapp.adapter")

**Why this module exists.** Bringing Aegis up used to be an *ordered* sequence of ten
``configure_*`` calls writing ~35 process-global singletons, three of which fired as
module-import side effects — so import order was a hidden dependency with nothing to read
it off. ``arXiv 2603.15159`` benchmarks exactly this case and finds that complete, correct
specs for every API move first-attempt success by only +5 to +8 points; its dominant error
class is *omitted necessary operations in a required sequence*. A sequence you cannot get
wrong beats a sequence documented correctly, so the sequence is gone.

**The globals stay.** This is a composition root, not a rewrite: ripping out 35 call sites
would buy no behavioural change, and the contract is what this phase is about.
:func:`aegis.governance.configure_governance` already proved the pattern — it exists
solely to compose ``configure_enforcement`` + ``configure_audit`` — and nobody generalised
it. This generalises it.

**It is a composition root, not a facade.** It wires; it does not run. There is no
``.query()``, no ``.chat()``, no ``.ingest()`` here and there must never be one: the moment
this object grows behaviour of its own, every component behind it has two entry points and
the contract is back to being prose.

Four questions this module answers deliberately, because getting any of them wrong
reproduces the defect class it exists to remove:

**What it reads, and what it refuses to guess.** It reads the ``AEGIS_``-prefixed
environment through :class:`aegis.core.config.CoreSettings` (``AEGIS_MODE``,
``AEGIS_VECTOR_STORE_PATH``, ``AEGIS_DATABASE_URL``, ``AEGIS_REDIS_URL``) plus
``AEGIS_JWT_SECRET`` / ``AEGIS_JWT_ALGORITHM`` / ``AEGIS_JWT_EXPIRE_MINUTES``. It refuses
to guess the **adapter** (no default domain exists) and the **database session factory**
(the host owns its engine, its pool and — decisively — which Postgres *role* serves
requests; inventing an engine here would quietly re-create the ``BYPASSRLS`` hole the
serving/owner DSN split closed). In ``full`` mode a missing one of either is a hard error
naming the parameter. A constructor that silently invents a default is the same defect
class as a silent vector-store downgrade.

**Handle, not a one-shot bootstrap.** ``from_env`` returns an :class:`Aegis` you keep. It
carries the resolved mode, the adapter it verified, and :attr:`Aegis.seams` — one
:class:`Seam` row per wiring decision, with where the value came from. That record is the
thing that makes an ephemeral choice *audible*: :meth:`Aegis.describe` prints it and
``from_env`` logs it, so "this process indexes into RAM and loses it on restart" is a line
in the boot log rather than a discovery made three days later.

**A second call is refused, not merged.** Two adapters in one process would leave the ~35
globals holding a mix of both — the memory spec from one domain, the tool registry from
another — which is exactly the working-looking-but-wrong state this phase removes. A
second ``from_env`` naming a *different* adapter raises
:class:`AegisAlreadyConfiguredError`; the same adapter re-wires idempotently (the values
are identical), and ``replace=True`` is the explicit escape hatch for tests and notebooks.

**A missing piece of environment fails loud, naming the variable and the value shape.**
Not "configuration error" — the variable, what it should contain, and the honest
alternative. That is what let this project recover from a Temporal misconfiguration in two
minutes.
"""

from __future__ import annotations

import importlib
import logging
import secrets
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from pydantic_settings import BaseSettings, SettingsConfigDict

from aegis.adapter import DomainAdapter, missing_members
from aegis.core.config import ENV_PREFIX, AegisMode, CoreSettings

if TYPE_CHECKING:  # pragma: no cover - typing-only imports, never a runtime dependency
    from collections.abc import Awaitable, Callable
    from types import ModuleType

    from sqlalchemy.ext.asyncio import AsyncSession

    from aegis.core.interfaces import ChatCompleter
    from aegis.gateway import GatewayConfig, GovernanceHook, ObservabilitySink

logger = logging.getLogger(__name__)

__all__ = [
    "Aegis",
    "AegisAlreadyConfiguredError",
    "AegisError",
    "AdapterContractError",
    "MissingConfigurationError",
    "Seam",
    "active",
]

#: The shortest ``AEGIS_JWT_SECRET`` a non-lite process may sign with. Mirrors the
#: backend's own ``MIN_JWT_SECRET_LEN``: an HS256 secret shorter than the digest it keys
#: adds nothing over a 32-byte one and is trivially in range of an offline attack.
MIN_JWT_SECRET_LEN = 32


class AegisError(RuntimeError):
    """Base class for every failure :meth:`Aegis.from_env` raises."""


class AdapterContractError(AegisError):
    """The adapter could not be imported, or does not satisfy :class:`~aegis.adapter.DomainAdapter`.

    Raised **before any seam is written**, deliberately. An adapter missing
    ``memory_spec`` used to surface as a ``RuntimeError`` from
    :func:`aegis.memory.spec.get_default_spec` on the first turn that tried to recall
    anything — at a call site with no connection to the thing that was wrong. Naming the
    missing member at bring-up costs one ``hasattr`` per member and turns a debugging
    session into a sentence.
    """


class MissingConfigurationError(AegisError):
    """A required piece of environment or a required host seam is absent.

    The message names the environment variable (or the ``from_env`` parameter), the shape
    of the value it wants, and the honest alternative — never just "not configured".
    """


class AegisAlreadyConfiguredError(AegisError):
    """``from_env`` was called a second time with a *different* adapter.

    The ~35 process globals are one set, so a second domain does not get its own; it
    overwrites some of the first one's entries and leaves the rest. Pass ``replace=True``
    if replacing really is what you want.
    """


@dataclass(frozen=True, slots=True)
class Seam:
    """One wiring decision, and where its value came from.

    The point of recording this is that a *choice* and an *omission* look identical from
    the outside once the process is running. An ephemeral vector store is a legitimate
    choice for dev, tests and offline evals — and a catastrophe when it happened because
    nobody set ``AEGIS_VECTOR_STORE_PATH``. The difference is whether anything said so.

    Attributes:
        target: The seam that was written, spelled as the call an integrator would have
            made by hand (``"aegis.memory.set_default_index"``).
        source: Where the value came from — ``"env AEGIS_VECTOR_STORE_PATH"``,
            ``"adapter.memory_spec"``, ``"host session_factory"``, ``"caller override"``.
        detail: What was actually installed, in one line.
        durable: ``False`` marks a deliberately non-durable choice, so
            :meth:`Aegis.describe` and the boot log can shout about it.
    """

    target: str
    source: str
    detail: str
    durable: bool = True


class _RuntimeEnv(BaseSettings):
    """The ``AEGIS_``-prefixed variables :class:`~aegis.core.config.CoreSettings` does not carry.

    Kept separate rather than added to ``CoreSettings`` because ``CoreSettings`` is the
    *infrastructure* contract (which backends exist, and where) and these are the signing
    contract. Same prefix, same fail-fast posture, different question.
    """

    model_config = SettingsConfigDict(env_prefix=ENV_PREFIX, extra="ignore")

    jwt_secret: str | None = None
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 720


#: The handle for the currently-configured process, or ``None``. Module-level because the
#: thing it guards is module-level: the globals ``from_env`` writes are per-process, so the
#: record of what wrote them has to be too.
_ACTIVE: Aegis | None = None


def active() -> Aegis | None:
    """Return the :class:`Aegis` handle this process was brought up with, if any.

    For code that is *inside* the process and did not do the bringing up (a route, a
    worker activity, a test helper). It is deliberately nullable: a component must not
    assume it was reached through ``from_env``, because for the whole strangler period it
    can also be reached through the historical import-time shims.
    """
    return _ACTIVE


@dataclass(frozen=True, slots=True)
class Aegis:
    """A configured Aegis process: what was wired, from where, and in which mode.

    Build one with :meth:`from_env`; there is no other supported constructor, and
    instantiating this class directly wires nothing.

    Attributes:
        adapter: The verified domain adapter (in practice the imported package).
        adapter_module: The dotted module path it was imported from, or ``"<object>"``
            when an already-imported module was handed in.
        domain_id: ``adapter.DOMAIN_ID`` — the domain this process is serving.
        mode: The **resolved** :class:`~aegis.core.config.AegisMode`, not the declared
            one. ``auto`` has already been probed by the time this exists.
        settings: The :class:`~aegis.core.config.CoreSettings` that were read.
        seams: One :class:`Seam` per wiring decision, in the order they were made.
    """

    adapter: DomainAdapter
    adapter_module: str
    domain_id: str
    mode: AegisMode
    settings: CoreSettings
    seams: tuple[Seam, ...] = field(default_factory=tuple)

    # ─────────────────────────────────────────────────────────────── the way up ──
    @classmethod
    async def from_env(  # noqa: C901, PLR0912, PLR0913, PLR0915 - a composition root is a list of seams; splitting it hides the order
        cls,
        *,
        adapter: str | ModuleType | object,
        session_factory: Callable[[], AsyncSession] | None = None,
        set_tenant_scope: Callable[[AsyncSession, int | None], Awaitable[None]] | None = None,
        gateway_config: GatewayConfig | None = None,
        governance: GovernanceHook | None = None,
        observability: ObservabilitySink | None = None,
        completer: ChatCompleter | None = None,
        enqueue_approval: Callable[..., Awaitable[Any]] | None = None,
        approval_model: Any = None,  # noqa: ANN401 - host ORM class, kept loose (see configure_ops)
        approval_status: Any = None,  # noqa: ANN401 - host status enum, kept loose
        mode: AegisMode | str | None = None,
        vector_store_path: str | None = None,
        database_url: str | None = None,
        redis_url: str | None = None,
        jwt_secret: str | None = None,
        jwt_algorithm: str | None = None,
        jwt_expire_minutes: int | None = None,
        replace: bool = False,
    ) -> Aegis:
        """Bring the process up: verify the adapter, read the environment, wire every seam.

        The order below is the order that used to be an integrator's problem, and every
        step of it is here because skipping it produced a *working-looking* system:

        1. **Import and verify the adapter** — before anything else, so a missing member
           is named at bring-up rather than at the first turn that needed it.
        2. **Read the environment and resolve the mode** — ``auto`` genuinely probes.
           ``full`` with an unset backend variable raises here, naming the variables.
        3. **Refuse a second, different domain** (see :class:`AegisAlreadyConfiguredError`).
        4. **Write the seams**, recording each one.

        Args:
            adapter: The domain adapter — a dotted module path (``"myapp.adapter"``,
                imported here) or an already-imported module/object. **Required, with no
                default**: there is no generic domain, and a platform that invented one
                would answer questions about a domain nobody wrote.
            session_factory: Zero-argument callable returning an
                :class:`~sqlalchemy.ext.asyncio.AsyncSession`, used as
                ``async with session_factory() as session``. **Required in ``full``
                mode**, and deliberately not derived from ``AEGIS_DATABASE_URL``: the
                host owns the engine, the pool and — decisively — *which Postgres role*
                serves requests. A role with ``BYPASSRLS`` leaves every tenant-isolation
                policy enforced against nobody, which is not a choice this function may
                make on a host's behalf.
            set_tenant_scope: The RLS scope binder. Defaults to
                :func:`aegis.governance.rls.set_tenant_scope` inside each data-layer
                module; pass one only if the host needs to observe or wrap it.
            gateway_config: The model-gateway connection + call-safety settings. Omitted,
                :mod:`aegis.gateway` reads its own configuration from the environment.
            governance: The budget/rate enforcement hook. **Omitted, the gateway's no-op
                hook stands, which means no spend enforcement at all** — recorded as a
                non-durable seam and logged, because an uncapped gateway that looks
                healthy is the expensive version of a silent downgrade.
            observability: The span/usage sink. Omitted, this wires
                :class:`aegis.observability.OtelObservabilitySink` when it is importable.
            completer: The cheap-model chat completer the NeMo Colang custom actions use
                for their model-based rails. Omitted, those rails stay deterministic-only
                (their documented offline backstop).
            enqueue_approval: The durable approval writer for the LLM-Ops release gate.
            approval_model: The host's ``Approval`` ORM class.
            approval_status: The host's approval-status enum.
            mode: Override for ``AEGIS_MODE``. For a host that already has its own
                notion of dev-vs-production (the backend's ``stores_enabled`` /
                ``is_dev``) this is how it stays the single source of that truth instead
                of duplicating it into a second variable. Recorded as ``caller override``.
            vector_store_path: Override for ``AEGIS_VECTOR_STORE_PATH``, same reasoning.
            database_url: Override for ``AEGIS_DATABASE_URL``. It is only ever *read* —
                to answer ``require_full_infra``'s question "is a relational store
                configured at all" — and never dialled: the engine comes from
                ``session_factory``, for the reason spelled out there. A host that owns
                its own DSN setting passes it here instead of duplicating the value into
                a second environment variable, which is how two DSNs drift apart.
            redis_url: Override for ``AEGIS_REDIS_URL``, same reasoning.
            jwt_secret: Override for ``AEGIS_JWT_SECRET``.
            jwt_algorithm: Override for ``AEGIS_JWT_ALGORITHM``.
            jwt_expire_minutes: Override for ``AEGIS_JWT_EXPIRE_MINUTES``.
            replace: Allow replacing an already-configured *different* adapter.

        Returns:
            The :class:`Aegis` handle. Keep it: :meth:`describe` is the record of every
            choice made on your behalf.

        Raises:
            AdapterContractError: The adapter module cannot be imported, or is missing a
                :class:`~aegis.adapter.DomainAdapter` member (each one named).
            MissingConfigurationError: A required variable or host seam is absent. The
                message names the variable, the value shape, and the honest alternative.
            AegisAlreadyConfiguredError: A different adapter is already configured and
                ``replace`` is ``False``.
            RuntimeError: From :meth:`~aegis.core.config.CoreSettings.require_full_infra`
                when ``AEGIS_MODE=full`` and a backend variable is unset — it already
                names the variables and the ``lite`` escape hatch.
        """
        global _ACTIVE  # noqa: PLW0603 - one deliberate process-wide registration

        # ── 1. the adapter, before anything else ───────────────────────────────
        domain, module_path = _load_adapter(adapter)
        domain_id = str(getattr(domain, "DOMAIN_ID", "") or "").strip()
        if not domain_id:
            raise AdapterContractError(
                f"{module_path} has a DOMAIN_ID that is empty or blank. It is the "
                "stable machine id of the loaded domain and it is written onto "
                "generated datasets and traces, so it cannot be blank. Set "
                'DOMAIN_ID = "your_domain" in the adapter package.'
            )

        # ── 2. refuse a second, different domain ───────────────────────────────
        if _ACTIVE is not None and not replace and _ACTIVE.domain_id != domain_id:
            raise AegisAlreadyConfiguredError(
                f"This process is already configured for domain "
                f"{_ACTIVE.domain_id!r} (from {_ACTIVE.adapter_module}); "
                f"{module_path} declares {domain_id!r}. Aegis writes process-global "
                "singletons, so a second domain would not get its own set — it would "
                "overwrite part of the first one's and leave the rest, which is the "
                "half-wired state this entry point exists to make impossible. Run the "
                "second domain in its own process, or pass replace=True if replacing "
                "really is what you want."
            )

        # ── 3. the environment ─────────────────────────────────────────────────
        settings = CoreSettings()
        if mode is not None:
            settings = settings.model_copy(update={"mode": AegisMode(mode)})
        for name, value in (
            ("vector_store_path", vector_store_path),
            ("database_url", database_url),
            ("redis_url", redis_url),
        ):
            if value is not None:
                settings = settings.model_copy(update={name: value})
        resolved = await settings.resolve_mode()
        durable_infra = resolved is AegisMode.full

        env = _RuntimeEnv()
        seams: list[Seam] = []

        # ── 4. the seams ───────────────────────────────────────────────────────
        _wire_memory_spec(domain, module_path, seams)
        _wire_vector_stores(
            settings,
            resolved,
            mode_source="caller override" if mode is not None else f"{ENV_PREFIX}MODE",
            path_source=(
                "caller override"
                if vector_store_path is not None
                else f"env {ENV_PREFIX}VECTOR_STORE_PATH"
            ),
            seams=seams,
        )
        _wire_security(env, jwt_secret, jwt_algorithm, jwt_expire_minutes, durable_infra, seams)
        _wire_gateway(gateway_config, governance, observability, seams)
        _wire_governance(session_factory, set_tenant_scope, durable_infra, seams)
        _wire_ops(
            domain,
            session_factory,
            set_tenant_scope,
            enqueue_approval,
            approval_model,
            approval_status,
            seams,
        )
        _wire_jobs(session_factory, seams)
        _wire_guardrails(domain, completer, seams)

        runtime = cls(
            adapter=domain,
            adapter_module=module_path,
            domain_id=domain_id,
            mode=resolved,
            settings=settings,
            seams=tuple(seams),
        )
        _ACTIVE = runtime
        logger.info("Aegis is up.\n%s", runtime.describe())
        for seam in seams:
            if not seam.durable:
                logger.warning("Aegis %s: %s", seam.target, seam.detail)
        return runtime

    # ────────────────────────────────────────────────────────────────── readers ──
    def describe(self) -> str:
        """Return the boot record: the domain, the resolved mode, and every seam.

        This is the artifact, not a nicety. "Which of the ten seams did my bring-up
        actually write, and from what?" had no answer at all before this object existed,
        and it is the first question anyone asks when retrieval returns nothing.
        """
        lines = [
            f"Aegis — domain {self.domain_id!r} from {self.adapter_module}",
            f"  mode: {self.mode.value} (resolved)",
        ]
        width = max((len(s.target) for s in self.seams), default=0)
        for seam in self.seams:
            mark = " " if seam.durable else "!"
            lines.append(f"  {mark} {seam.target:<{width}}  {seam.detail}  [{seam.source}]")
        return "\n".join(lines)

    @property
    def ephemeral_seams(self) -> tuple[Seam, ...]:
        """The seams that are deliberately non-durable in this process.

        Empty is the production expectation; non-empty is legitimate for dev, tests and
        offline evals — and is the thing to check first when something "worked yesterday".
        """
        return tuple(s for s in self.seams if not s.durable)

    # ─────────────────────────────────────────────────────────────── teardown ──
    @staticmethod
    def reset() -> None:
        """Forget the active handle and clear the two seams that can be cleared.

        Deliberately honest about its own limits. The vector seams have documented
        reset functions (:func:`aegis.retrieval.reset_vector_store`,
        :func:`aegis.memory.reset_default_index`) and are cleared, so the next use
        raises its named error again. The rest — the gateway hooks, the JWT config, the
        governance session factory, the memory spec — have no unwire, by design: they
        are overwritten by the next :meth:`from_env`, never restored to "unset". This
        exists for test isolation, not as a lifecycle.
        """
        global _ACTIVE  # noqa: PLW0603 - the counterpart of the registration above
        _ACTIVE = None
        try:
            from aegis.retrieval import reset_vector_store
        except ImportError:  # pragma: no cover - the retrieval extra is optional
            pass
        else:
            reset_vector_store()
        try:
            from aegis.memory import reset_default_index
        except ImportError:  # pragma: no cover - the retrieval extra is optional
            pass
        else:
            reset_default_index()


# ─────────────────────────────────────────────────────────────────── the adapter ──
def _load_adapter(adapter: str | ModuleType | object) -> tuple[DomainAdapter, str]:
    """Import ``adapter`` if it is a path, then verify it against the Protocol.

    Both halves are load-bearing and both name what is wrong. An ``ImportError`` from a
    dotted path is re-raised as an :class:`AdapterContractError` carrying the original
    message, because "no module named myapp.adaptor" is a typo an integrator fixes in
    five seconds *if they see it*, and a bare traceback out of a startup hook is not
    where they look.
    """
    if isinstance(adapter, str):
        try:
            module: Any = importlib.import_module(adapter)
        except ImportError as exc:
            raise AdapterContractError(
                f"Could not import the adapter module {adapter!r}: {exc}. "
                "adapter= takes the dotted path of your adapter *package* (the one "
                "whose __init__.py re-exports the nine pieces), e.g. "
                '"myapp.adapter" — and that package must be importable from this '
                "process (on sys.path / pip-installed)."
            ) from exc
        module_path = adapter
    else:
        module = adapter
        module_path = getattr(adapter, "__name__", "<object>")

    missing = missing_members(module)
    if missing:
        raise AdapterContractError(
            f"{module_path} is missing {len(missing)} DomainAdapter member(s): "
            f"{', '.join(missing)}. Every member is a piece the platform genuinely "
            "reads — see aegis.adapter for what each one is and who consumes it. Note "
            "that a submodule becomes an attribute of its package only once something "
            "imports it, so an adapter whose __init__.py never touches a piece does "
            "NOT have that member however present the file is on disk: importing it "
            "in __init__.py is usually the whole fix. Checked with "
            "aegis.adapter.missing_members(), which you can run yourself before "
            "calling this."
        )
    return module, module_path


# ───────────────────────────────────────────────────────────────────── the seams ──
def _wire_memory_spec(domain: DomainAdapter, module_path: str, seams: list[Seam]) -> None:
    """Seam 1 — ``aegis.memory.set_default_spec``, from the adapter's piece 7."""
    from aegis.memory import set_default_spec

    set_default_spec(domain.memory_spec)  # type: ignore[arg-type]  # structural MemorySpec
    seams.append(
        Seam(
            target="aegis.memory.set_default_spec",
            source=f"{module_path}.memory_spec",
            detail=f"{len(list(domain.memory_spec.FACT_TYPES))} fact type(s), "
            f"skills from {domain.memory_spec.SKILLS_DIR}",
        )
    )


def _wire_vector_stores(
    settings: CoreSettings,
    resolved: AegisMode,
    *,
    mode_source: str,
    path_source: str,
    seams: list[Seam],
) -> None:
    """Seams 2 and 3 — the two that used to degrade silently (§8.4).

    ``full`` gets the durable on-disk engine at the resolved vector-store path;
    :meth:`~aegis.core.config.CoreSettings.require_full_infra` has already raised by now
    if it is unset, so there is no branch here where a path is invented. ``lite`` gets
    the ephemeral in-process engine — the honest dev/test choice — recorded as
    ``durable=False`` so it is logged at WARNING and printed with a ``!`` rather than
    being indistinguishable from the durable case.

    ``mode_source``/``path_source`` are threaded in rather than assumed, because a host
    that owns its own config names passes both as arguments; reporting those as "read
    from AEGIS_VECTOR_STORE_PATH" would send the next person to debug this to a variable
    nobody set. A record that misnames where a value came from is worse than none.

    Both branches configure. Neither leaf has an implicit default any more, so a process
    that skipped this block would get a named error out of the first recall — but it
    would get it *late*, and this function exists so that never happens.
    """
    try:
        from aegis.memory import MemoryVectorIndex, set_default_index
        from aegis.retrieval import ChromaVectorStore, configure_vector_store
    except ImportError as exc:  # pragma: no cover - the retrieval extra is optional
        seams.append(
            Seam(
                target="aegis.retrieval.configure_vector_store",
                source="unavailable",
                detail=f"NOT configured: {exc}. Install the vector tier with "
                "`pip install aegis[retrieval]`; until then any component that needs "
                "an index raises VectorStoreNotConfiguredError.",
                durable=False,
            )
        )
        return

    if resolved is AegisMode.full:
        path = str(settings.vector_store_path)
        set_default_index(MemoryVectorIndex.local(path=path))
        configure_vector_store(lambda: ChromaVectorStore.local(path=path))
        detail = f"durable, on disk at {path}"
        durable = True
        source = path_source
    else:
        set_default_index(MemoryVectorIndex.local())
        configure_vector_store(ChromaVectorStore.local)
        detail = (
            "EPHEMERAL in-process engine — nothing indexed in this process survives it. "
            f"Chosen because the resolved mode is {resolved.value} (from {mode_source}); "
            "a durable store needs mode=full and a vector-store path."
        )
        durable = False
        source = f"{mode_source}={resolved.value}"

    seams.append(Seam("aegis.memory.set_default_index", source, detail, durable=durable))
    seams.append(
        Seam("aegis.retrieval.configure_vector_store", source, detail, durable=durable)
    )


def _wire_security(
    env: _RuntimeEnv,
    jwt_secret: str | None,
    jwt_algorithm: str | None,
    jwt_expire_minutes: int | None,
    durable_infra: bool,
    seams: list[Seam],
) -> None:
    """Seam 4 — ``aegis.governance.security.configure_security`` (was an import side effect).

    The one place this function generates a value rather than reading one: with no secret
    configured in a ``lite`` process it mints a random per-process secret. That is not an
    invented *default* — a fixed dev default is what would be, and it is precisely how a
    forgotten ``AEGIS_JWT_SECRET`` reaches production signing tokens anyone can forge.
    A random one cannot be shared, cannot be committed, and is recorded as non-durable
    because every token it signs dies with the process.

    In ``full`` mode there is no such kindness: the variable is named, its shape is
    named, and the process does not come up without it.
    """
    from aegis.governance.security import DEFAULT_JWT_SECRET, configure_security

    secret = jwt_secret if jwt_secret is not None else env.jwt_secret
    algorithm = jwt_algorithm if jwt_algorithm is not None else env.jwt_algorithm
    minutes = jwt_expire_minutes if jwt_expire_minutes is not None else env.jwt_expire_minutes
    source = "caller override" if jwt_secret is not None else f"env {ENV_PREFIX}JWT_SECRET"
    durable = True

    if durable_infra:
        if not secret:
            raise MissingConfigurationError(
                f"{ENV_PREFIX}JWT_SECRET is unset and this process resolved to "
                f"{ENV_PREFIX}MODE=full. Every access token this deployment issues is "
                "signed with it, so an unset one is not a degraded feature — it is "
                "unauthenticated access. Set it to a random string of at least "
                f"{MIN_JWT_SECRET_LEN} characters (generate one with: python -c "
                "'import secrets; print(secrets.token_urlsafe(48))'), or pass "
                "jwt_secret=... if your host already owns the value. In "
                f"{ENV_PREFIX}MODE=lite a random per-process secret is minted instead."
            )
        if secret == DEFAULT_JWT_SECRET:
            raise MissingConfigurationError(
                f"{ENV_PREFIX}JWT_SECRET is the shipped dev placeholder, and this "
                f"process resolved to {ENV_PREFIX}MODE=full. That value is in the "
                "source tree, so anyone can mint an admin token for this deployment. "
                f"Replace it with a random string of at least {MIN_JWT_SECRET_LEN} "
                "characters."
            )
        if len(secret) < MIN_JWT_SECRET_LEN:
            raise MissingConfigurationError(
                f"{ENV_PREFIX}JWT_SECRET is {len(secret)} characters; a non-lite "
                f"deployment needs at least {MIN_JWT_SECRET_LEN}. It keys HS256, whose "
                "digest is 32 bytes, so a shorter secret buys nothing and is in range "
                "of an offline search."
            )
    elif not secret:
        secret = secrets.token_urlsafe(48)
        source = "generated"
        durable = False

    configure_security(secret, algorithm, minutes)
    seams.append(
        Seam(
            target="aegis.governance.security.configure_security",
            source=source,
            detail=(
                f"{algorithm}, {minutes} min"
                if durable
                else f"{algorithm}, {minutes} min — RANDOM per-process secret; every "
                "token issued here is invalid after a restart. Set "
                f"{ENV_PREFIX}JWT_SECRET for a stable one."
            ),
            durable=durable,
        )
    )


def _wire_gateway(
    gateway_config: GatewayConfig | None,
    governance: GovernanceHook | None,
    observability: ObservabilitySink | None,
    seams: list[Seam],
) -> None:
    """Seam 5 — ``aegis.gateway.configure`` (was an import side effect at ``app/core/llm.py``).

    ``observability`` defaults to the standalone OTel sink because that is a decision with
    no host-specific content: it emits spans, and a host that wants none can pass its own
    no-op. ``gateway_config`` and ``governance`` get no default here: ``configure`` rebinds
    only the arguments that are not ``None``, so passing nothing deliberately *keeps*
    whatever is already bound.

    **The report reads the bindings back rather than echoing the arguments**, and that
    distinction is the whole point of this function. Throughout the strangler period a
    host's import-time shim is a legitimate second configurer — ``app.core.llm`` binds a
    real governance hook the moment it is imported — so "I was passed ``governance=None``"
    and "this process has no spend enforcement" are different facts. Reporting the first
    as the second would have printed ``NO budget/rate enforcement`` on every boot of a
    fully-governed deployment, which is how a warning stops being read.
    """
    import aegis.gateway as gateway
    from aegis.gateway import llm as _gateway_state

    sink = observability
    sink_source = "caller"
    if sink is None:
        try:
            from aegis.observability import OtelObservabilitySink
        except ImportError:  # pragma: no cover - otel is optional
            sink_source = "unavailable"
        else:
            sink = OtelObservabilitySink()
            sink_source = "default"

    gateway.configure(config=gateway_config, governance=governance, observability=sink)

    # Read back what is actually bound now, whoever bound it.
    bound_config = _gateway_state._config
    bound_governance = _gateway_state._governance
    if gateway_config is not None:
        config_source = "caller"
    elif bound_config is not None:
        config_source = "already bound (host import-time shim)"
    else:
        config_source = "env (gateway defaults)"
    seams.append(
        Seam(
            target="aegis.gateway.configure",
            source=config_source,
            detail=f"config={config_source}, observability={sink_source}",
        )
    )

    enforcing = not isinstance(bound_governance, _gateway_state._NoOpGovernance)
    if enforcing:
        seams.append(
            Seam(
                target="aegis.gateway.configure(governance=)",
                source="caller" if governance is not None else "already bound (host shim)",
                detail=f"{type(bound_governance).__name__} — budget/rate enforcement is on",
            )
        )
    else:
        seams.append(
            Seam(
                target="aegis.gateway.configure(governance=)",
                source="not provided",
                detail="NO budget/rate enforcement on model calls — the gateway's "
                "built-in hook is fail-open by construction. Pass governance=... to "
                "bind spend caps.",
                durable=False,
            )
        )


def _wire_governance(
    session_factory: Callable[[], AsyncSession] | None,
    set_tenant_scope: Callable[[AsyncSession, int | None], Awaitable[None]] | None,
    durable_infra: bool,
    seams: list[Seam],
) -> None:
    """Seams 6 and 7 — ``configure_governance``, composing enforcement + audit.

    This is the function the phase doc points at as the pattern nobody generalised: it
    exists solely to compose two sub-configures. Everything above and below is that idea
    taken to its conclusion.
    """
    if session_factory is None:
        if durable_infra:
            raise MissingConfigurationError(
                "session_factory= is required when the resolved mode is full. Aegis "
                "does not build an engine from AEGIS_DATABASE_URL on your behalf: the "
                "host owns the pool and, decisively, *which Postgres role* serves "
                "requests. A role with BYPASSRLS leaves every tenant-isolation policy "
                "installed and enforced against nobody, which is not a choice this "
                "function may make for you. Pass a zero-argument callable returning an "
                "AsyncSession — e.g. session_factory=lambda: my_sessionmaker() — or "
                f"set {ENV_PREFIX}MODE=lite to run with no relational store."
            )
        seams.append(
            Seam(
                target="aegis.governance.configure_governance",
                source="not provided",
                detail="NOT configured (no session_factory, mode is lite): budget "
                "enforcement, the usage ledger and the audit log all raise a named "
                "error on first use.",
                durable=False,
            )
        )
        return

    from aegis.governance import configure_governance

    configure_governance(session_factory=session_factory, set_tenant_scope=set_tenant_scope)
    detail = "enforcement + audit bound to the host session factory"
    seams.append(Seam("aegis.governance.configure_enforcement", "host session_factory", detail))
    seams.append(Seam("aegis.governance.configure_audit", "host session_factory", detail))


def _wire_ops(
    domain: DomainAdapter,
    session_factory: Callable[[], AsyncSession] | None,
    set_tenant_scope: Callable[[AsyncSession, int | None], Awaitable[None]] | None,
    enqueue_approval: Callable[..., Awaitable[Any]] | None,
    approval_model: Any,  # noqa: ANN401 - host ORM class (see configure_ops)
    approval_status: Any,  # noqa: ANN401 - host status enum
    seams: list[Seam],
) -> None:
    """Seam 8 — ``aegis.ops.configure_ops`` (was an import side effect at ``app/ops/__init__.py``).

    The floor renderer is **derived from the adapter**, not asked of the host. It was a
    host seam only because nothing could reach the adapter's pieces generically before
    :class:`~aegis.adapter.DomainAdapter` existed; the backend's version of it is exactly
    ``render_system_prompt(get_persona(prompt_key))``, which pieces 5 and 6 now spell.
    One fewer thing an integrator can omit.
    """
    from aegis.ops import configure_ops

    def _render_floor(prompt_key: str) -> str:
        """The adapter/persona prompt floor for ``prompt_key`` (pieces 5 + 6)."""
        return domain.prompts.render_system_prompt(domain.personas.get_persona(prompt_key))

    configure_ops(
        render_floor_prompt=_render_floor,
        session_factory=session_factory,
        set_tenant_scope=set_tenant_scope,
        enqueue_approval=enqueue_approval,
        approval_model=approval_model,
        approval_status=approval_status,
    )
    gate = "with the durable approval gate" if enqueue_approval is not None else "no approval gate"
    seams.append(
        Seam(
            target="aegis.ops.configure_ops",
            source="adapter.prompts + adapter.personas",
            detail=f"prompt floor from the adapter, {gate}",
        )
    )


def _wire_jobs(session_factory: Callable[[], AsyncSession] | None, seams: list[Seam]) -> None:
    """Seam 9 — ``aegis.jobs.set_activity_session_factory``.

    Worker-side rather than API-side, and wired here anyway: the in-process launch mode
    runs the worker in this very process, and a bring-up that configured eight seams and
    left the ninth to a different file is exactly the ordering hazard this replaces.
    With no session factory there is nothing to bind and the job scope keeps its own
    named error.
    """
    if session_factory is None:
        return
    try:
        from aegis.jobs import set_activity_session_factory
    except ImportError:  # pragma: no cover - temporal is an optional extra
        return
    set_activity_session_factory(session_factory)
    seams.append(
        Seam(
            target="aegis.jobs.set_activity_session_factory",
            source="host session_factory",
            detail="durable job activities run in the host's session scope",
        )
    )


def _wire_guardrails(
    domain: DomainAdapter, completer: ChatCompleter | None, seams: list[Seam]
) -> None:
    """Seam 10 — the NeMo Colang rails: allowed topics from the adapter, completer from the host.

    ``DOMAIN_DESCRIPTION`` is a *control input*, not metadata: it becomes the topical
    rail's allowed-topic set, so a vague description is a loose rail. Wiring it from the
    adapter here is what stops the Colang engine and the programmatic pipeline disagreeing
    about what this deployment is allowed to talk about.
    """
    try:
        from aegis.guardrails import nemo
    except ImportError:  # pragma: no cover - the guardrails extras are optional
        return
    nemo.set_allowed_topics(domain.DOMAIN_DESCRIPTION)
    seams.append(
        Seam(
            target="aegis.guardrails.nemo.set_allowed_topics",
            source="adapter.DOMAIN_DESCRIPTION",
            detail=f"{len(domain.DOMAIN_DESCRIPTION)} chars of allowed-topic description",
        )
    )
    if completer is not None:
        nemo.set_completer(completer)
        seams.append(
            Seam(
                target="aegis.guardrails.nemo.set_completer",
                source="host completer",
                detail="model-based Colang rails (injection + content safety) are live",
            )
        )
