"""The one transient-retry primitive, shared by the graph nodes and the sub-agents.

There is exactly one implementation of "does this policy consider this exception
retryable, and how long do I wait between attempts" in the package, and it lives here.
It was extracted the moment a *second* caller appeared (the sub-agent loop, whose model
calls need the same ``_MODEL_RETRY`` the graph's model nodes have always had), because
two copies of a retry predicate is how one of them quietly stops honouring a policy
shape the other still does.

Nothing here knows about graph state, nodes or events: it takes a zero-argument async
factory and a :class:`~langgraph.types.RetryPolicy`.
"""

from __future__ import annotations

import asyncio
import logging
import random
from collections.abc import Awaitable, Callable
from typing import TypeVar

from langgraph.errors import GraphBubbleUp
from langgraph.types import RetryPolicy

__all__ = ["call_with_retry", "should_retry"]

logger = logging.getLogger(__name__)

T = TypeVar("T")


def should_retry(policy: RetryPolicy, exc: Exception) -> bool:
    """Return whether ``policy`` classifies ``exc`` as retryable.

    Honours all three shapes LangGraph's :class:`~langgraph.types.RetryPolicy` accepts for
    ``retry_on``: a callable predicate (the default ``default_retry_on``, which admits
    only transient classes), a single exception type, or a sequence of them.
    """
    retry_on = policy.retry_on
    if callable(retry_on) and not isinstance(retry_on, type):
        return bool(retry_on(exc))
    if isinstance(retry_on, type):
        return isinstance(exc, retry_on)
    return isinstance(exc, tuple(retry_on))


async def call_with_retry(
    factory: Callable[[], Awaitable[T]],
    *,
    policy: RetryPolicy | None,
    label: str,
) -> T:
    """Await ``factory()`` under ``policy``, retrying only provably transient failures.

    Args:
        factory: A zero-argument callable returning the awaitable to attempt. It is
            called once per attempt, so a fresh coroutine is created each time.
        policy: The retry policy; ``None`` means a single attempt.
        label: What is being retried, for the warning log line.

    Returns:
        Whatever ``factory()`` resolved to.

    Raises:
        Exception: The last failure, once attempts are exhausted or the policy declines
            to retry it. :class:`~langgraph.errors.GraphBubbleUp` (interrupts and
            commands) is control flow, never a failure, and is re-raised immediately.
    """
    if policy is None:
        return await factory()
    attempt = 1
    interval = policy.initial_interval
    while True:
        try:
            return await factory()
        except GraphBubbleUp:
            raise
        except Exception as exc:  # noqa: BLE001 - re-raised unless provably transient
            if attempt >= policy.max_attempts or not should_retry(policy, exc):
                raise
            delay = min(interval, policy.max_interval)
            if policy.jitter:
                delay += random.uniform(0, 1)
            logger.warning(
                "%s attempt %d/%d failed (%s); retrying in %.2fs",
                label,
                attempt,
                policy.max_attempts,
                exc,
                delay,
            )
            await asyncio.sleep(delay)
            interval = min(interval * policy.backoff_factor, policy.max_interval)
            attempt += 1
