"""The real Ragas metrics, run over the seed corpus through Aegis's own gateway.

This module is the answer to a claim the evals screen has been making about itself: that
one cell of the score matrix — *answer relevancy* — is left empty rather than filled with
a number the platform could not defend. That was honest, and it was honest because
relevancy cannot be faked with token overlap: it needs a generated question embedded and
compared against the original, which is a model call and an embedding call.

So the cell stays empty until someone asks for it to be filled, and then it is filled with
a real Ragas score.

**Why this is a separate, explicitly-triggered path.** ``GET /evals/report`` is a
sub-second deterministic call, memoised for the process, and a dashboard polls it. Running
LLM-judged metrics on that route would turn a page refresh into a budget event. Everything
here costs model calls and says so.

**Everything runs through the gateway.** Not a convenience — the adapters exist so that
every judge call is budget-checked, rate-limited, traced and written to the usage ledger.
An evaluation subsystem whose spend is invisible to the platform's own cost surface would
be the one place the metering claim is false.
"""

from __future__ import annotations

import asyncio
import math
from dataclasses import dataclass
from typing import Any

__all__ = ["LiveMetric", "run_ragas_suite"]


@dataclass(frozen=True, slots=True)
class LiveMetric:
    """One metric, computed by the real library.

    Attributes:
        name: Namespaced with the library that produced it — ``ragas:answer_relevancy``
            — so a reader can never mistake it for one of the deterministic proxies that
            share its subject.
        value: The score in ``[0, 1]``, or ``None`` when the metric could not be run.
        cases: How many cases contributed.
        library: The library and version that produced it, for the receipt.
        note: Why the value is ``None``, when it is. Empty otherwise.
    """

    name: str
    value: float | None
    cases: int
    library: str
    note: str = ""


def _usable(value: Any) -> float | None:  # noqa: ANN401 - ragas MetricResult.value
    """Turn one ragas score into a number, or into ``None`` with no number at all.

    Two of ragas's own routine outcomes are not measurements, and passing either through
    would break the rule this module is built on — *a zero is a measurement, and not
    running is not a measurement*.

    **NaN.** ``Faithfulness`` returns ``float("nan")`` when the judge produced no
    statements, which is an ordinary LLM outcome rather than an error. NaN is not
    ``None``, so it used to enter the sample, count toward ``cases``, and poison the
    mean — **one NaN case turned the whole metric NaN even when every other case
    scored**. Pydantic then serialised it as ``null``, so the panel showed an empty value
    with no note beside a claim that two cases contributed: a reader told two cases were
    scored and shown nothing.

    **Zero from a failed judge.** ``AnswerRelevancy`` returns ``0.0`` when the judge
    generated no question to compare against. That is a judge failure reported as perfect
    irrelevance, and it drags the corpus mean down with a number nobody measured. This
    module guards its own zero-avoidance carefully; importing one from the library it
    trusts would be the same defect at one remove.

    A real 0.0 is indistinguishable from a failed-judge 0.0 at this boundary, so this
    treats both as not-run. That is the safe direction: under-reporting a genuinely
    terrible score costs a point of sensitivity, while over-reporting a judge outage as a
    measured zero is a fabricated number on a screen whose whole subject is not
    fabricating numbers.
    """
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(out) or out == 0.0:
        return None
    return out


async def run_ragas_suite(
    *,
    complete: Any,  # noqa: ANN401 - aegis.gateway.complete, or a double
    embed: Any,  # noqa: ANN401 - aegis.gateway.embed, or a double
    limit: int = 3,
) -> list[LiveMetric]:
    """Score the seed corpus with Ragas, on the gateway.

    Args:
        complete: The gateway's chat entry point.
        embed: The gateway's embedding entry point.
        limit: How many seed cases to score. Small by default and stated on the panel:
            every case is several model calls, and a button that quietly costs more the
            longer the corpus grows is a button somebody eventually regrets pressing.

    Returns:
        One :class:`LiveMetric` per metric. A metric that could not run reports ``None``
        with the reason, never a zero — a zero is a measurement, and not running is not
        a measurement.
    """
    from importlib.metadata import version

    from aegis.evals.corpus import SEED_CORPUS, SEED_CASES
    from aegis.evals.libs.gateway_adapters import AegisGatewayEmbedding, AegisGatewayLLM

    lib = f"ragas@{version('ragas')}"
    cases = list(SEED_CASES)[:limit]
    docs = {d.id: d.text for d in SEED_CORPUS}

    llm = AegisGatewayLLM(complete)
    embedder = AegisGatewayEmbedding(embed)

    from ragas.metrics.collections import AnswerRelevancy, Faithfulness

    async def _one(case: Any) -> tuple[float | None, float | None]:  # noqa: ANN401
        contexts = [docs[d] for d in case.gold_doc_ids if d in docs]
        if not contexts:
            return None, None
        # The "answer" under test is the grounded context itself. That is deliberate and
        # worth stating: this measures whether the METRICS work end to end against real
        # content, not whether some particular generator is good. Scoring a generated
        # answer needs a generation call per case, which is the next increment and a
        # different, larger claim.
        answer = " ".join(contexts)[:800]
        try:
            faith = await Faithfulness(llm=llm).ascore(
                user_input=case.query, response=answer, retrieved_contexts=contexts
            )
            # No contexts: relevancy asks whether the ANSWER answers the QUESTION,
            # by generating questions the answer would suit and embedding them against
            # the original. Retrieval quality is faithfulness's job, above.
            rel = await AnswerRelevancy(llm=llm, embeddings=embedder).ascore(
                user_input=case.query, response=answer
            )
            return _usable(faith.value), _usable(rel.value)
        except (TypeError, AttributeError):
            # A wrong call signature is OUR bug, not a judge outage, and it must not be
            # reported as one. This exact catch already lied once: `ascore()` was called
            # with an argument it does not take, and the panel said "the judge or the
            # embedder was unavailable" — sending a reader to check a model deployment
            # that was working perfectly.
            raise
        except Exception:  # noqa: BLE001 - a judge outage is not a score
            return None, None

    results = await asyncio.gather(*(_one(c) for c in cases))
    faiths = [f for f, _ in results if f is not None]
    rels = [r for _, r in results if r is not None]

    def _mean(xs: list[float]) -> float | None:
        return sum(xs) / len(xs) if xs else None

    def _note(scored: list[float], when_none: str) -> str:
        """Say what the sample is, whenever it is not what was asked for.

        ``cases`` reports how many cases produced a usable score, and a reader comparing
        it against the ``limit`` they requested deserves to be told where the difference
        went rather than left to infer it. Silence here is how a mean over one case came
        to look like a mean over three.
        """
        if not scored:
            return when_none
        dropped = len(cases) - len(scored)
        if dropped:
            return (
                f"{dropped} of {len(cases)} case(s) produced no usable score — the judge "
                "returned nothing, or returned a value that is not a measurement — and "
                "are excluded from this mean rather than counted as zero"
            )
        return ""

    return [
        LiveMetric(
            name="ragas:faithfulness",
            value=_mean(faiths),
            cases=len(faiths),
            library=lib,
            note=_note(faiths, "the judge returned nothing usable; not scored"),
        ),
        LiveMetric(
            name="ragas:answer_relevancy",
            value=_mean(rels),
            cases=len(rels),
            library=lib,
            note=_note(rels, "the judge or the embedder was unavailable; not scored"),
        ),
    ]
