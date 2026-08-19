"""``@tenant_activity`` — the structural guarantee that background work stays scoped.

Phase 1 made tenant isolation provable, and there is exactly one way that guarantee dies
in a job substrate: an activity opens a session without binding a tenant scope. It then
runs on the *serving* engine — the ``NOSUPERUSER NOBYPASSRLS`` role the policies really
apply to — with ``app.tenant_id`` unset, and the ``tenant_isolation`` predicate's
documented fail-open branch makes every tenant's rows visible to it. Nothing errors.
Nothing logs. The ingest for tenant A quietly reads and rewrites tenant B's documents.

A convention cannot prevent that, because the failure mode of a convention is that
somebody forgets. So this module makes it structural in three ways at once:

1. **The tenant comes from the activity's own typed argument.** Never a contextvar, and
   this is the load-bearing sentence in the module. Ambient context does not survive a
   replay in a fresh worker process — the process that resumes an interrupted run is not
   the process that started it, and it inherits nothing. An activity that *silently*
   loses its scope on replay is the worst version of this bug: correct on the happy
   path, unscoped exactly when a crash has already made everyone stop looking.
2. **No argument, no run.** :class:`MissingTenantScopeError` is raised before the
   activity body executes — at decoration time when the signature makes it provable, and
   at call time otherwise.
3. **The activity cannot open its own session.** It is *handed* one, already scoped,
   already inside a transaction, by :func:`tenant_activity`. There is no supported path
   by which an activity obtains an unscoped session, which is a stronger statement than
   "activities remember to call ``set_tenant_scope``".

The scope survives a commit
---------------------------

:func:`aegis.governance.rls.set_tenant_scope` writes the GUC with ``is_local=true``, so
Postgres discards it at the end of the transaction — deliberately, because a
session-level ``SET`` would leak one tenant's scope into the next request that borrowed
the pooled connection. The consequence for a long activity is sharp: **an activity that
commits and then keeps working would continue unscoped.** So the binding here is not a
one-shot call but an ``after_begin`` listener on the session, which re-binds the scope at
the start of *every* transaction the activity opens. The one-transaction stage-commit
rule makes that redundant in the normal case; it is here because "redundant in the normal
case" is not the same as "cannot happen".

The transaction boundary is the stage commit rule
-------------------------------------------------

The decorator commits once, after the activity returns, and rolls back on any exception.
That is the phase's stage commit rule expressed as code rather than as a paragraph: an
activity writes its output *and* bumps ``completed_stage`` in one transaction, so a stage
that "finished" but whose output rolled back is not a state the schema can reach.
"""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from functools import wraps
from typing import TYPE_CHECKING, Any, ParamSpec, TypeVar, overload

if TYPE_CHECKING:  # pragma: no cover - typing only
    from sqlalchemy.ext.asyncio import AsyncSession

__all__ = [
    "ActivityInput",
    "MissingTenantScopeError",
    "SessionFactoryNotConfiguredError",
    "activity_session_factory",
    "reset_activity_session_factory",
    "set_activity_session_factory",
    "tenant_activity",
]

P = ParamSpec("P")
T = TypeVar("T")

#: The name of the field an activity's argument must carry, and of the keyword the
#: decorator injects. Both are constants rather than literals sprinkled through the
#: module because the error messages quote them and a typo in one of those would produce
#: a diagnostic that sends the reader to the wrong place.
_TENANT_FIELD = "tenant_id"
_SESSION_KEYWORD = "session"


class MissingTenantScopeError(RuntimeError):
    """An activity was defined or invoked without a tenant to scope it to.

    Raised in three situations, all of which mean the same thing — this work has no
    owner, so running it would run it against everybody:

    * the decorated function takes no positional parameter at all;
    * its first parameter's type resolves to a class with no ``tenant_id`` field (caught
      at **decoration** time, so the failure lands at import rather than at 3 a.m. on the
      first ingest);
    * the argument passed at call time carries no ``tenant_id``, or carries ``None``
      without the activity having opted in to platform scope.

    It is a ``RuntimeError`` rather than a ``ValueError`` because it is a statement about
    the state of the program, not about a value a caller could have corrected: an
    activity that reaches this point is one whose isolation guarantee is absent.
    """


class SessionFactoryNotConfiguredError(RuntimeError):
    """A tenant activity ran before the host wired a session factory into this module.

    ``aegis.jobs`` deliberately does not know how to build a database session — the DSNs,
    the engine split and the pool live in the composing application. The host installs
    its factory once at worker bootstrap via :func:`set_activity_session_factory`.

    Failing loudly here rather than lazily constructing *some* engine is the point: a
    substrate that silently invents its own connection would invent one on the wrong DSN,
    which on this platform means the owner role — the one that bypasses RLS.
    """


@dataclass(frozen=True, slots=True)
class ActivityInput:
    """The base shape of every tenant activity's single argument.

    Frozen because an activity argument crosses a serialisation boundary and may be
    replayed: a value that could be mutated in the activity body would differ between the
    original run and the replay, and the whole point of a durable engine is that those
    two are the same. ``slots`` because these are allocated per stage per document.

    Subclasses add whatever the stage needs. ``tenant_id`` stays here so that the one
    field the decorator reads is declared once, in the base, and cannot be forgotten by a
    subclass author who is thinking about document ids.

    Attributes:
        tenant_id: The owning tenant, or ``None`` for genuinely platform-level work. See
            :func:`tenant_activity`: ``None`` is refused unless the activity explicitly
            declares ``allow_platform_scope``, so the unscoped case is always a decision
            somebody wrote down.
        workflow_id: The orchestrator's execution id, carried so an activity can find
            its own :class:`aegis.jobs.JobRun` row without the orchestrator's context
            APIs — which is what keeps the record layer readable when the orchestrator is
            swapped or switched off.
    """

    tenant_id: int | None
    workflow_id: str


#: The host's session factory. A module-level slot rather than a decorator argument
#: because activities are declared at import time, long before any engine exists.
#:
#: Note the asymmetry with the tenant scope, which is emphatically *not* stored ambiently:
#: a session factory is a property of the **process** (one engine, one pool, one DSN) and
#: is identical on every replay in every worker, whereas a tenant is a property of the
#: **work** and differs per call. Storing the first here is configuration; storing the
#: second here would be the bug this module exists to prevent.
_session_factory: Callable[[], AsyncSession] | None = None


def set_activity_session_factory(factory: Callable[[], AsyncSession] | None) -> None:
    """Install the session factory every tenant activity will open its session from.

    Called once by the host at worker bootstrap, with the same factory the request path
    uses — which matters, because that factory is bound to the *serving* engine and the
    serving engine is the one the RLS policies actually apply to. Handing this the admin
    engine would make every activity bypass row security and every isolation test that
    used it vacuous.

    Args:
        factory: A zero-argument callable returning a new ``AsyncSession`` (an
            ``async_sessionmaker`` is exactly this), or ``None`` to unwire it.
    """
    global _session_factory
    _session_factory = factory


def reset_activity_session_factory() -> None:
    """Unwire the session factory.

    For tests and for a host that rebuilds its wiring: leaving a factory bound to a
    disposed engine is how a suite gets an error about a closed pool three files later.
    """
    set_activity_session_factory(None)


def activity_session_factory() -> Callable[[], AsyncSession]:
    """Return the configured session factory.

    Returns:
        The factory installed by :func:`set_activity_session_factory`.

    Raises:
        SessionFactoryNotConfiguredError: If the host has not installed one.
    """
    if _session_factory is None:
        raise SessionFactoryNotConfiguredError(
            "no session factory is wired into aegis.jobs.scope, so a tenant activity has "
            "no scoped session to run on. The host must call "
            "set_activity_session_factory(...) with its serving-engine sessionmaker at "
            "worker bootstrap."
        )
    return _session_factory


def _first_parameter(fn: Callable[..., Any]) -> inspect.Parameter | None:
    """Return the first positional parameter of ``fn``, or ``None`` if it has none.

    Args:
        fn: The function being decorated.

    Returns:
        The parameter that will receive the activity's typed argument.
    """
    kinds = (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
    for parameter in inspect.signature(fn).parameters.values():
        if parameter.kind in kinds:
            return parameter
    return None


def _annotation_declares_tenant(fn: Callable[..., Any], parameter: str) -> bool | None:
    """Say whether ``fn``'s ``parameter`` is annotated with a type carrying ``tenant_id``.

    This is the decoration-time half of the guarantee: when the annotation resolves to a
    real class, an activity whose argument type has no ``tenant_id`` is rejected at
    import, which is enormously cheaper than discovering it on the first live ingest.

    Args:
        fn: The function being decorated.
        parameter: The name of its first positional parameter.

    Returns:
        ``True`` when the annotation resolves and declares the field, ``False`` when it
        resolves and does not, and ``None`` when no verdict is possible — an unannotated
        parameter, or a forward reference that cannot be resolved from this module. The
        third case is not a failure: the call-time check still covers it, and refusing to
        decorate a function whose annotations merely happen to be un-resolvable here
        would break legitimate code for no gain in safety.
    """
    from typing import get_type_hints  # noqa: PLC0415 - local: keeps import cost off the hot path

    try:
        hints = get_type_hints(fn)
    except (NameError, TypeError):
        # A forward reference to a name not importable from this module, or a non-class
        # annotation. Narrow on purpose: anything broader here would swallow the very
        # verdict this function exists to produce.
        return None
    annotation = hints.get(parameter)
    if annotation is None or not isinstance(annotation, type):
        return None
    if _TENANT_FIELD in getattr(annotation, "__annotations__", {}):
        return True
    return any(
        _TENANT_FIELD in getattr(base, "__annotations__", {}) for base in annotation.__mro__
    )


def _read_tenant_id(args: tuple[Any, ...], activity_name: str) -> int | None:
    """Extract the tenant id from an activity's arguments, or refuse to continue.

    Args:
        args: The positional arguments the activity was called with.
        activity_name: The activity's name, for the error message.

    Returns:
        The tenant id carried by the first positional argument. May be ``None``; the
        caller decides whether ``None`` is admissible.

    Raises:
        MissingTenantScopeError: If there is no positional argument, or it carries no
            ``tenant_id`` attribute.
    """
    if not args:
        raise MissingTenantScopeError(
            f"activity {activity_name!r} was called with no arguments, so there is no "
            f"{_TENANT_FIELD} to scope it to. Every tenant activity takes one typed "
            "argument carrying the tenant — never an ambient context, which does not "
            "survive a replay in a fresh worker."
        )
    payload = args[0]
    if not hasattr(payload, _TENANT_FIELD):
        raise MissingTenantScopeError(
            f"activity {activity_name!r} was called with a "
            f"{type(payload).__name__} argument, which has no {_TENANT_FIELD} field. "
            "Running it would open an unscoped session on the serving engine and read "
            "every tenant's rows."
        )
    return getattr(payload, _TENANT_FIELD)


def _bind_scope_on_every_transaction(session: AsyncSession, tenant_id: int | None) -> None:
    """Re-bind the tenant scope at the start of every transaction on ``session``.

    ``set_tenant_scope`` writes a transaction-local GUC, so the scope evaporates on
    commit. This listener is what makes the binding a property of the *session* for the
    activity's whole lifetime rather than of one transaction — see the module docstring.

    The listener is registered on the underlying sync session because that is where
    SQLAlchemy's ORM events live; it runs inside the greenlet that drives the async
    session, so issuing SQL from it is legitimate and not a blocking call on the event
    loop.

    Args:
        session: The activity's session.
        tenant_id: The scope to bind, or ``None`` for platform scope (which asserts
            ``app.tenant_all='on'``, exactly as ``set_tenant_scope`` does — the two
            share :data:`~aegis.governance.rls.SCOPE_BINDING_SQL` so there is one
            definition of what "bound" means, and the scope auditor sees both).
    """
    # One definition of "re-bind on every transaction", shared with every other path
    # that commits more than once — see the helper for why that shape is the auditor's
    # most common real finding.
    from aegis.governance.rls import (  # noqa: PLC0415 - local: aegis.jobs.scope stays import-light
        bind_scope_for_session,
    )

    bind_scope_for_session(session, tenant_id)


def _scoped_call(
    fn: Callable[..., Awaitable[T]], *, allow_platform_scope: bool
) -> Callable[..., Awaitable[T]]:
    """Build the wrapper that binds the scope, runs the activity and commits once.

    Args:
        fn: The activity body. It must accept a ``session`` keyword argument.
        allow_platform_scope: Whether ``tenant_id=None`` is admissible.

    Returns:
        The wrapped activity.
    """
    activity_name = getattr(fn, "__qualname__", getattr(fn, "__name__", "<activity>"))

    @wraps(fn)
    async def wrapper(*args: Any, **kwargs: Any) -> T:  # noqa: ANN401
        tenant_id = _read_tenant_id(args, activity_name)
        if tenant_id is None and not allow_platform_scope:
            raise MissingTenantScopeError(
                f"activity {activity_name!r} was called with {_TENANT_FIELD}=None. An "
                "unscoped session on the serving engine sees every tenant's rows, so "
                "platform scope is never implicit: declare "
                "@tenant_activity(allow_platform_scope=True) if that is genuinely what "
                "this activity does."
            )
        from aegis.governance.context import (  # noqa: PLC0415 - local: see module docstring
            governed,
        )
        from aegis.governance.rls import (  # noqa: PLC0415 - local: see module docstring
            set_tenant_scope,
        )

        session = activity_session_factory()()
        _bind_scope_on_every_transaction(session, tenant_id)
        try:
            # Binds the scope on the transaction that is open *now*. The listener above
            # covers every transaction after this one; this call covers the first, and
            # keeps the shared seam in aegis.governance.rls on the normal path rather
            # than reachable only through an event handler.
            await set_tenant_scope(session, tenant_id)
            # ...and the *billing* scope, from the same field, for the same reason. An
            # activity that embeds a corpus spends real money at the gateway, and the
            # gateway can only cap and ledger what a bound governance context names. Two
            # scopes, one source: the tenant on the activity's own argument.
            with governed(tenant_id=tenant_id):
                result = await fn(*args, **kwargs, **{_SESSION_KEYWORD: session})
            # One commit, after the body returns: the stage commit rule. The activity's
            # output and its completed_stage bump land together or not at all.
            await session.commit()
            return result
        except BaseException:
            # Deliberately BaseException: a cancelled activity (the orchestrator's
            # cancellation signal arrives as CancelledError, which is not an Exception)
            # must not leave a half-written stage committed by the connection's return
            # to the pool.
            await session.rollback()
            raise
        finally:
            await session.close()

    return wrapper


def _install_public_signature(wrapper: Any, fn: Callable[..., Any]) -> None:  # noqa: ANN401
    """Publish the activity's *wire* signature: one typed argument, no ``session``.

    An orchestrator SDK reads the activity's signature and type hints to decide how to
    deserialise its argument, and both need correcting after wrapping:

    * **The signature** must not show the ``session`` the decorator supplies. It is a
      keyword-only parameter no caller ever passes and no payload ever encodes, and
      Temporal's ``@activity.defn`` rejects keyword-only parameters outright — correctly,
      since it has no way to fill one from a serialised argument list.
    * **The annotations must be resolved types, not strings.** ``functools.wraps`` copies
      ``__annotations__`` verbatim, and under ``from __future__ import annotations`` those
      are strings that only resolve against the *body's* module globals. The wrapper's
      globals are this module's, so an SDK calling ``typing.get_type_hints`` on it would
      raise ``NameError`` for a perfectly ordinary activity. Resolving here, once at
      decoration time, is what keeps the wrapped activity introspectable.

    Args:
        wrapper: The wrapper produced by :func:`_scoped_call`.
        fn: The original activity body.
    """
    from typing import get_type_hints  # noqa: PLC0415 - local, mirrors this module's style

    signature = inspect.signature(fn)
    wrapper.__signature__ = signature.replace(
        parameters=[
            parameter
            for name, parameter in signature.parameters.items()
            if name != _SESSION_KEYWORD
        ]
    )
    try:
        annotations: dict[str, Any] = dict(get_type_hints(fn))
    except (NameError, TypeError):
        # An annotation naming something importable only under TYPE_CHECKING. Narrow on
        # purpose: falling back to the raw strings leaves the wrapper no worse than an
        # undecorated function, whereas a broad handler here would hide a real mistake.
        annotations = dict(getattr(fn, "__annotations__", {}))
    annotations.pop(_SESSION_KEYWORD, None)
    wrapper.__annotations__ = annotations


@overload
def tenant_activity(fn: Callable[P, Awaitable[T]]) -> Callable[..., Awaitable[T]]: ...


@overload
def tenant_activity(
    *, allow_platform_scope: bool = False
) -> Callable[[Callable[P, Awaitable[T]]], Callable[..., Awaitable[T]]]: ...


def tenant_activity(
    fn: Callable[P, Awaitable[T]] | None = None, *, allow_platform_scope: bool = False
) -> Any:  # noqa: ANN401 - two call shapes; the overloads above carry the real types
    """Bind the tenant scope for an activity, and refuse to run without one.

    Phase 1 made tenant isolation provable, and the way that guarantee dies is an
    activity that opens a session without binding a scope: it runs unscoped on the
    serving engine and reads every tenant's rows. A convention cannot prevent that —
    somebody forgets — so this is structural.

    The tenant comes from the activity's own typed argument, never from a contextvar:
    ambient context does not survive a replay in a fresh worker process, and an activity
    that *silently* loses its scope on replay is the worst version of this bug.

    The decorated function is called with its own argument plus a ``session`` keyword the
    decorator supplies: a session from the host's serving-engine factory, with the tenant
    scope bound and re-bound on every transaction. The decorator commits it once when the
    body returns and rolls it back on any exception, which is the phase's stage commit
    rule. An activity therefore never opens a session of its own, and there is no
    supported path by which it could obtain an unscoped one.

    Usable bare or parameterised::

        @activity.defn(name="run_stage")
        @tenant_activity
        async def run_stage(inp: StageInput, *, session: AsyncSession) -> StageOutcome: ...

        @tenant_activity(allow_platform_scope=True)
        async def roll_partitions(inp: PlatformInput, *, session: AsyncSession) -> None: ...

    Args:
        fn: The activity body, when used bare. ``None`` when used with arguments.
        allow_platform_scope: Whether ``tenant_id=None`` may run. Defaults to ``False``,
            so unscoped execution is never implicit — under the ``tenant_isolation``
            predicate an unbound scope deliberately fails *open*, which makes "I forgot
            to set the tenant" and "this is platform work" indistinguishable at the
            database. This flag is what makes them distinguishable in the source.

    Returns:
        The wrapped activity (bare form), or a decorator (parameterised form).

    Raises:
        MissingTenantScopeError: At decoration time, if the function takes no positional
            parameter, or its first parameter's type resolves to a class with no
            ``tenant_id`` field. At call time, if the argument carries no ``tenant_id``,
            or carries ``None`` without ``allow_platform_scope``.
        TypeError: At decoration time, if the function is not a coroutine function, or
            does not accept the ``session`` keyword the decorator injects. Both would
            otherwise surface as an obscure ``TypeError`` from deep inside a worker.
    """

    def decorate(target: Callable[P, Awaitable[T]]) -> Callable[..., Awaitable[T]]:
        name = getattr(target, "__qualname__", repr(target))
        if not inspect.iscoroutinefunction(target):
            raise TypeError(
                f"{name} is not a coroutine function; a tenant activity awaits a scoped "
                "session, so it must be `async def`"
            )
        parameters = inspect.signature(target).parameters
        if _SESSION_KEYWORD not in parameters:
            raise TypeError(
                f"{name} must accept a `{_SESSION_KEYWORD}` keyword argument: "
                "@tenant_activity supplies the scoped session, and an activity that "
                "opens its own would be the unscoped read this decorator exists to "
                "prevent."
            )
        first = _first_parameter(target)
        if first is None:
            raise MissingTenantScopeError(
                f"{name} declares no positional parameter, so it can carry no "
                f"{_TENANT_FIELD}. Every tenant activity takes one typed argument."
            )
        if _annotation_declares_tenant(target, first.name) is False:
            raise MissingTenantScopeError(
                f"{name}'s argument {first.name!r} is annotated with a type that declares "
                f"no {_TENANT_FIELD} field. Subclass aegis.jobs.scope.ActivityInput, or "
                "add the field: an activity with no tenant on its argument cannot be "
                "scoped at all."
            )
        wrapper = _scoped_call(target, allow_platform_scope=allow_platform_scope)
        _install_public_signature(wrapper, target)
        return wrapper

    if fn is not None:
        return decorate(fn)
    return decorate
