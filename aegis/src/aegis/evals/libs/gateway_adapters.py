"""Ragas, running on Aegis's own metered gateway.

There are two ways to point an evaluation library at a model, and only one of them is
compatible with what this platform claims about itself.

The quick way is a ``base_url``. It works in ten minutes and it routes every judge call
**around** :func:`aegis.gateway.complete` — no budget check, no usage-ledger row, no rate
limiter, no OTel span, no circuit breaker, no role→deployment routing. On a platform whose
pitch is that every model call is metered and attributable, that would make the evaluation
subsystem the one place where it is not. The numbers on the evals screen would be produced
by calls the cost dashboard cannot see.

So instead: adapters over the gateway's own entry points. Ragas exposes exactly the seam
for it — two abstract methods on the LLM, two on the embedder — and implementing them is
cheaper than the ``base_url`` shortcut is dishonest.

Three details that are not incidental:

* **DeepSeek wraps JSON in a ``<think>`` preamble.** The judge module already solved this
  and its stripper is reused rather than re-derived; neither Ragas nor DeepEval handles it,
  and a structured-output parse that fails on the reasoning model's own habit would look
  exactly like a bad answer.
* **A budget refusal must not become a zero.** ``complete`` raises when a tenant is over
  its cap. That exception is allowed to propagate so the metric is reported as *not run*,
  following the contract :class:`~aegis.evals.judge.JudgeUnavailableError` already sets. A
  zero would be a measurement nobody made.
* **Async is the hot path.** Ragas calls ``agenerate``; ``generate`` exists as a bridge and
  should not be the one that runs.
"""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING, Any, TypeVar

from aegis.core.models import ModelRole
from aegis.evals.judge import JudgeUnavailableError, _json_candidates

if TYPE_CHECKING:  # pragma: no cover - import-time only
    from collections.abc import Awaitable, Callable, Sequence

__all__ = ["AegisGatewayEmbedding", "AegisGatewayLLM"]

T = TypeVar("T")


def _import_ragas_bases() -> tuple[type, type]:
    """Import Ragas's abstract bases lazily.

    Deferred so that importing :mod:`aegis.evals` never pulls Ragas in — a property
    ``tests/evals/test_isolation.py`` asserts, and one worth keeping: the core eval
    library stays dependency-free and offline, and the real libraries are an opt-in
    extra rather than a cost every consumer pays.
    """
    from ragas.embeddings.base import BaseRagasEmbedding
    from ragas.llms.base import InstructorBaseRagasLLM

    return InstructorBaseRagasLLM, BaseRagasEmbedding


def AegisGatewayLLM(  # noqa: N802 - a factory that returns a class instance
    complete: Callable[..., Awaitable[Any]],
    *,
    role: ModelRole = ModelRole.REASONING,
) -> Any:  # noqa: ANN401 - the Ragas base is imported lazily
    """A Ragas judge whose every call goes through the Aegis gateway.

    Args:
        complete: :func:`aegis.gateway.complete`, or a test double with its signature.
        role: Which model role judges. ``REASONING`` is documented as the LLM-as-judge
            role, so this is the same seat the existing hand-rolled judge sits in.

    Returns:
        An object satisfying Ragas's ``InstructorBaseRagasLLM``.
    """
    base, _ = _import_ragas_bases()

    class _GatewayLLM(base):  # type: ignore[misc, valid-type]
        def __init__(self) -> None:
            self.model = f"aegis-gateway:{role.value}"
            self.provider = "aegis-gateway"
            self.is_async = True

        async def agenerate(self, prompt: str, response_model: type[T]) -> T:
            schema = json.dumps(response_model.model_json_schema())  # type: ignore[attr-defined]
            result = await complete(
                role,
                [
                    {
                        "role": "system",
                        "content": (
                            "Reply with ONLY a JSON object matching this schema. No "
                            f"prose, no code fence.\n{schema}"
                        ),
                    },
                    {"role": "user", "content": prompt},
                ],
                temperature=0.0,
            )
            content = getattr(result, "content", "") or ""
            for candidate in _json_candidates(content):
                try:
                    return response_model.model_validate_json(candidate)  # type: ignore[attr-defined]
                except Exception:  # noqa: BLE001 - try the next salvage
                    continue
            raise JudgeUnavailableError(
                "the judge returned nothing parseable as the requested schema; the "
                "metric is not run rather than scored zero."
            )

        def generate(self, prompt: str, response_model: type[T]) -> T:
            # A bridge, not the hot path. Ragas drives `agenerate`.
            return asyncio.run(self.agenerate(prompt, response_model))

    return _GatewayLLM()


def AegisGatewayEmbedding(  # noqa: N802 - a factory that returns a class instance
    embed: Callable[[Sequence[str]], Awaitable[Sequence[Sequence[float]]]],
) -> Any:  # noqa: ANN401 - the Ragas base is imported lazily
    """A Ragas embedder on the Aegis gateway.

    Answer relevancy is the metric this exists for: it is the one the hand-rolled proxies
    could never compute, because it needs a real embedding rather than token overlap, and
    it is the empty cell on the evals screen.

    Args:
        embed: :func:`aegis.gateway.embed`, or a test double with its signature.
    """
    _, base = _import_ragas_bases()

    class _GatewayEmbedding(base):  # type: ignore[misc, valid-type]
        PROVIDER_NAME = "aegis-gateway"

        async def aembed_text(self, text: str, **_: Any) -> list[float]:
            vectors = await embed([text])
            return list(vectors[0])

        def embed_text(self, text: str, **_: Any) -> list[float]:
            return asyncio.run(self.aembed_text(text))

    return _GatewayEmbedding()
