"""Per-principal query-pattern analysis — the *extraction* half of AML.T0024.

**MITRE ATLAS AML.T0024, Exfiltration via AI Inference API.** The technique has two
halves and they need two different controls. The first half is the answer used as a
**channel** — an auto-loading image pointing at a collector — and that one is closed
deterministically on the outbound path by
:func:`aegis.guardrails.schema.exfiltration_channel`. The second half is extraction
**by volume**: model extraction by sweeping a scoring parameter, and membership
inference by walking a list of record ids. This module is the control for the second
half, and it exists because the first one structurally cannot answer it.

Why no text rail can answer this
--------------------------------
Every other rail in this platform screens one payload and asks whether *that text* is
safe. "Was customer record 4471 part of your training data?" is a legitimate question
about a record the asker is entitled to see. So is 4472. The attack is not in any one
query; it is in the fact that one principal asked four hundred of them in five minutes,
each identical but for the id. A rail that could see the attack in a single message
would have to guess at intent from wording, which is how a keyword list gets written and
how ordinary users start getting refused. So the unit of analysis here is **the
principal over a window**, not the message.

What it measures
----------------
Two deterministic signals, both computed from a masked *template* of each query
(:func:`query_signature`): identifiers, numbers, emails and opaque blobs are replaced by
tokens, and the remaining shape is hashed. Two queries that differ only in an id share a
template; two genuinely different questions do not.

* **Template enumeration** — one principal issues at least
  :attr:`ExtractionThresholds.min_template_repeats` queries sharing a single template
  inside the window, and those queries carry at least
  :attr:`ExtractionThresholds.min_distinct_values` *distinct* masked values. Both
  conditions are load-bearing. Without the repeat count a support agent checking ten
  tickets is a finding; without the distinct-value count a client retrying one flaky
  question forty times is a finding. Neither is an attack, and both happen daily.

* **Subject breadth** — one principal issues at least
  :attr:`ExtractionThresholds.min_total_queries` queries in the window touching at least
  :attr:`ExtractionThresholds.min_distinct_subjects` distinct identifiers *across all
  templates*. This is the answer to the obvious evasion: rotate the phrasing so no two
  queries hash alike, and keep sweeping the same id space. The query-count condition is
  what keeps a single legitimate bulk request — "summarise these eighty order ids" in
  one message — from being read as a sweep; eighty identifiers in one query is one
  query, and one query is never a pattern.

The window is what makes the counts safe. Thirty near-identical lookups spread across a
working morning is a person doing their job and is not a finding; thirty inside five
minutes is one every ten seconds, which is a script. The window is enforced against an
injected clock so that property is testable rather than asserted.

What it does **not** catch, stated plainly
------------------------------------------
* **A sweep under the floor.** An attacker who paces themselves below
  :attr:`~ExtractionThresholds.min_template_repeats` per window extracts the same
  information more slowly and this module never fires. That is a threshold, not an
  oversight: every rate-shaped detector has one, and lowering it trades directly against
  refusing real users. Two red-team probes (``exfil-06``, ``exfil-07``) are exactly this
  attack and are declared as leaks in the battery rather than argued away here.
* **An authorised bulk job.** A service principal running a nightly reconciliation over
  ten thousand records is indistinguishable from enumeration by these signals, because
  behaviourally it *is* enumeration. The findings are per principal, so an operator can
  see which principal it was — but there is no allowlist or service-account exemption
  built, so a deployment that runs such a job will see it flagged.
* **A distributed sweep.** The window is keyed per principal. An attacker holding two
  credentials halves their observed rate.

Storage: in memory, and what that costs
---------------------------------------
The window lives in this process's heap. It is deliberately **not** durable, and the
consequences are precise rather than hand-waved:

* **A restart clears every window.** An attacker who is 29 queries into a 30-query
  threshold when the process restarts starts again from zero.
* **It is per process, not per deployment.** Under ``uvicorn --workers N`` each worker
  keeps its own window, so a sweep spread across workers is observed at roughly ``1/N``
  of its true rate and the effective threshold is ``N`` times higher than configured.
  This platform runs the API in a single process (see ``app.main.lifespan``), which is
  why in-memory is honest here and would not be in a horizontally scaled one.
* **It is bounded.** At most :attr:`ExtractionThresholds.max_events_per_principal`
  events per principal and :attr:`ExtractionThresholds.max_principals` principals are
  retained; the least recently seen principal is evicted first. A detector that can be
  made to exhaust memory by opening accounts is a denial-of-service tool.

Nothing here does I/O, calls a model, or imports a driver. Scoring a query is a hash and
a walk over at most a few hundred timestamps.
"""

from __future__ import annotations

import hashlib
import re
import time
from collections import OrderedDict, deque
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum

from aegis.core.types import GuardResult, GuardVerdict

__all__ = [
    "EXTRACTION_LAYER",
    "ExtractionFinding",
    "ExtractionMonitor",
    "ExtractionSignal",
    "ExtractionThresholds",
    "QuerySignature",
    "query_signature",
]

#: The ``layer`` an extraction verdict is filed under, so a console can group and label
#: it apart from the text rails. It is a different kind of finding — about a principal's
#: behaviour, not about a payload — and sharing a label with ``injection`` would make the
#: two indistinguishable in the one place an operator looks.
EXTRACTION_LAYER = "extraction"


class ExtractionSignal(StrEnum):
    """Which of the two patterns fired.

    Named on the finding because the operator's next question is always "what did it
    see", and "systematic enumeration" and "abnormal breadth" call for different
    answers: the first names a template, the second names a count of subjects.
    """

    #: Many near-identical queries sweeping a varying value.
    ENUMERATION = "template_enumeration"
    #: Many queries touching an abnormal number of distinct subjects, phrasing aside.
    BREADTH = "subject_breadth"


# --- Templating ---------------------------------------------------------------
#: Ordered masking rules. **Order matters**: an email must be masked before the digits
#: inside it are, a UUID before its hex runs are, and an identifier like ``NW-4471``
#: before the bare-number rule splits it. Each rule replaces the value with a token and
#: the value itself is collected, because the count of *distinct* values is half of what
#: makes enumeration distinguishable from repetition.
_MASK_RULES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}"), "<email>"),
    (
        re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b"),
        "<uuid>",
    ),
    (re.compile(r"\b[0-9a-f]{16,}\b"), "<hex>"),
    # An identifier with a literal prefix: NW-4471, INV2231, ticket_88213, acct/55.
    (re.compile(r"\b[a-z]{2,12}[-_/]?\d{2,}\b"), "<id>"),
    (re.compile(r"-?\d+(?:\.\d+)?"), "<n>"),
)


@dataclass(frozen=True, slots=True)
class QuerySignature:
    """One query reduced to a template plus the values that varied.

    Attributes:
        template: A short hash of the masked text. Two queries differing only in an
            identifier share this.
        masked: The masked text itself, kept so a finding can *show* the operator the
            shape that was being swept rather than only a digest. Every identifier,
            number, email and opaque blob has already been replaced by a token, so this
            is the record-level-PII-free projection of the query — it is not a general
            redaction and the surrounding words are still the user's own.
        values: The masked-out values, in order of appearance. Counting the distinct
            members is what separates a sweep from a retry.
    """

    template: str
    masked: str
    values: tuple[str, ...]


def query_signature(text: str) -> QuerySignature:
    """Reduce ``text`` to its template and the values that were masked out of it.

    Pure and deterministic: no clock, no state, no I/O. Whitespace is collapsed and the
    text lowercased first so that formatting differences do not fork a template, then
    :data:`_MASK_RULES` are applied in order.

    Args:
        text: The query as the principal typed it.

    Returns:
        A :class:`QuerySignature`.
    """
    collected: list[str] = []
    masked = " ".join(text.split()).lower()
    for pattern, token in _MASK_RULES:

        def _replace(match: re.Match[str], _token: str = token) -> str:
            collected.append(match.group(0))
            return _token

        masked = pattern.sub(_replace, masked)
    digest = hashlib.sha256(masked.encode("utf-8")).hexdigest()[:16]
    return QuerySignature(template=digest, masked=masked, values=tuple(collected))


# --- Thresholds ---------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ExtractionThresholds:
    """The bounds the two signals are measured against.

    Every default below is a false-positive decision before it is a detection one, and
    the numbers are chosen against the rate a *person* can sustain rather than against a
    number that sounded strict.

    Attributes:
        window_seconds: How far back a finding may look. Five minutes: thirty queries
            inside it is one every ten seconds, which nobody types by hand, while the
            same thirty spread over a morning never co-occur in one window.
        min_template_repeats: Queries sharing one template before enumeration can fire.
        min_distinct_values: Distinct masked values those queries must carry. Set below
            ``min_template_repeats`` so a sweep with a few repeated ids still counts,
            and high enough that a retry loop (one value, many queries) cannot.
        min_total_queries: Queries in the window before the breadth signal can fire.
            Shared with the enumeration floor on purpose: one query is never a pattern,
            however many identifiers it happens to contain.
        min_distinct_subjects: Distinct identifiers across all templates for breadth.
            Twice the enumeration floor, because losing the template constraint means
            losing the strongest evidence and the count must carry more weight.
        max_events_per_principal: Ring-buffer size per principal.
        max_principals: How many principals are tracked before the least recently seen
            is evicted.
    """

    window_seconds: float = 300.0
    min_template_repeats: int = 30
    min_distinct_values: int = 25
    min_total_queries: int = 30
    min_distinct_subjects: int = 60
    max_events_per_principal: int = 512
    max_principals: int = 10_000


DEFAULT_THRESHOLDS = ExtractionThresholds()


@dataclass(frozen=True, slots=True)
class ExtractionFinding:
    """One principal's query pattern, judged.

    Attributes:
        signal: Which pattern fired (:class:`ExtractionSignal`).
        tenant_id: The tenant the principal belongs to, carried so every downstream
            surface — audit row, notification, console — is scoped by construction and
            cannot render one tenant's pattern to another.
        principal_id: The principal the window belongs to.
        queries: How many queries in the window support the finding.
        distinct_values: How many distinct identifiers those queries swept.
        window_seconds: The window the counts were measured over.
        template: The masked query shape being swept, for the enumeration signal;
            empty for breadth, which is by definition not one shape.
        reason: A sentence stating what was observed. Deliberately a description of
            behaviour and never an accusation about the last query's *text*: the query
            that trips this is, on its face, exactly as legitimate as the twenty-nine
            before it, and telling its author that their question was an attack would be
            both false and the fastest way to get the control switched off.
    """

    signal: ExtractionSignal
    tenant_id: str
    principal_id: str
    queries: int
    distinct_values: int
    window_seconds: float
    template: str
    reason: str


@dataclass(slots=True)
class _Event:
    """One observed query in a principal's window."""

    at: float
    template: str
    masked: str
    values: tuple[str, ...]


@dataclass(slots=True)
class _Window:
    """One principal's recent queries."""

    events: deque[_Event] = field(default_factory=deque)


class ExtractionMonitor:
    """Per-principal, tenant-scoped query-pattern analysis over a sliding window.

    One instance holds every tracked principal's window, keyed by
    ``(tenant_id, principal_id)``. **The tenant is part of the key and not merely a
    field on the finding**: two tenants that happen to issue the same principal id — a
    numeric user id from two different tables, an ``"api"`` service name in both — must
    never accumulate into one window, because that would let one tenant's traffic push
    another tenant's principal over a threshold and would put one tenant's query
    templates into a finding shown to the other.

    Thread-safety: this is a plain object with no lock. The platform's request path is a
    single-threaded asyncio loop and :meth:`observe` performs no ``await``, so it runs to
    completion between suspension points. A threaded host must serialise its own calls.

    Args:
        thresholds: The bounds to measure against. Defaults to
            :data:`DEFAULT_THRESHOLDS`.
        clock: A monotonic seconds source. Injected so the window's behaviour over time
            is testable without sleeping — the only way to assert that thirty lookups
            spread across a morning are *not* a finding.
    """

    def __init__(
        self,
        *,
        thresholds: ExtractionThresholds = DEFAULT_THRESHOLDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """Create an empty monitor."""
        self._thresholds = thresholds
        self._clock = clock
        self._windows: OrderedDict[tuple[str, str], _Window] = OrderedDict()

    @property
    def thresholds(self) -> ExtractionThresholds:
        """The bounds this monitor measures against."""
        return self._thresholds

    def reset(self) -> None:
        """Drop every tracked window. For tests and for an operator clearing a hold."""
        self._windows.clear()

    def observe(
        self, *, tenant_id: object, principal_id: object, text: str
    ) -> ExtractionFinding | None:
        """Record one query for a principal and judge the window it lands in.

        Args:
            tenant_id: The tenant scope. Stringified, so an ``int`` tenant id and a
                ``str`` one cannot key two windows for the same tenant.
            principal_id: The acting principal (user id, service name).
            text: The query the principal issued.

        Returns:
            An :class:`ExtractionFinding` when this query completes a pattern, otherwise
            ``None``. The query is recorded either way: a principal who keeps sweeping
            keeps producing findings until they stop for a whole window, which is what
            makes a refusal built on this decay by itself.
        """
        key = (str(tenant_id), str(principal_id))
        now = self._clock()
        window = self._windows.get(key)
        if window is None:
            window = _Window(deque(maxlen=self._thresholds.max_events_per_principal))
            self._windows[key] = window
        self._windows.move_to_end(key)
        self._evict_principals()

        signature = query_signature(text)
        window.events.append(
            _Event(
                at=now,
                template=signature.template,
                masked=signature.masked,
                values=signature.values,
            )
        )
        cutoff = now - self._thresholds.window_seconds
        while window.events and window.events[0].at < cutoff:
            window.events.popleft()

        return self._judge(key, window)

    def screen(
        self, *, tenant_id: object, principal_id: object, text: str
    ) -> GuardResult:
        """Observe a query and return the verdict a rail would.

        The adapter between this module and everything that speaks
        :class:`~aegis.core.types.GuardResult` — the red-team runner, a console, a host
        that wants the finding on the same path as its other rails.

        A finding is a **BLOCK**, not a flag, and that is a decision worth naming. A flag
        that lets query thirty-one through also lets thirty-two through, and the
        technique is completed by volume: an advisory on an extraction sweep is a record
        that the extraction happened. The gateway's RPM/TPM/USD caps bound what it
        *costs*, not what it discloses. The refusal is per principal and decays on its
        own once they stop for a window, and it names the observed behaviour rather than
        blaming the query.

        Args:
            tenant_id: The tenant scope.
            principal_id: The acting principal.
            text: The query.

        Returns:
            A ``BLOCK`` :class:`GuardResult` with ``layer="extraction"`` when a pattern
            fired, otherwise ``PASS``.
        """
        finding = self.observe(
            tenant_id=tenant_id, principal_id=principal_id, text=text
        )
        if finding is None:
            return GuardResult(
                verdict=GuardVerdict.PASS,
                reason="This principal's recent queries show no extraction pattern.",
                text=text,
            )
        return GuardResult(
            verdict=GuardVerdict.BLOCK,
            reason=finding.reason,
            text=text,
            layer=EXTRACTION_LAYER,
        )

    # --- internals ------------------------------------------------------------
    def _evict_principals(self) -> None:
        """Drop the least recently seen principals down to the configured cap."""
        while len(self._windows) > self._thresholds.max_principals:
            self._windows.popitem(last=False)

    def _judge(
        self, key: tuple[str, str], window: _Window
    ) -> ExtractionFinding | None:
        """Return a finding when ``window`` satisfies either signal, else ``None``."""
        tenant_id, principal_id = key
        bounds = self._thresholds
        events = window.events
        if len(events) < min(bounds.min_template_repeats, bounds.min_total_queries):
            return None

        per_template: dict[str, list[_Event]] = {}
        for event in events:
            per_template.setdefault(event.template, []).append(event)

        # Signal 1 — systematic enumeration of one query shape.
        for template_events in per_template.values():
            if len(template_events) < bounds.min_template_repeats:
                continue
            values = {v for e in template_events for v in e.values}
            if len(values) < bounds.min_distinct_values:
                continue
            masked = template_events[-1].masked
            return ExtractionFinding(
                signal=ExtractionSignal.ENUMERATION,
                tenant_id=tenant_id,
                principal_id=principal_id,
                queries=len(template_events),
                distinct_values=len(values),
                window_seconds=bounds.window_seconds,
                template=masked,
                reason=(
                    f"Systematic enumeration refused: this principal issued "
                    f"{len(template_events)} near-identical queries in the last "
                    f"{int(bounds.window_seconds)} seconds, sweeping "
                    f"{len(values)} distinct values through one query shape "
                    f"({masked!r}). No single one of those questions is a finding; "
                    "the rate and the sweep are. This is model extraction / membership "
                    "inference by query volume (MITRE ATLAS AML.T0024), and it clears "
                    "on its own once the principal stops for a full window."
                ),
            )

        # Signal 2 — abnormal breadth of subjects, whatever the phrasing.
        if len(events) >= bounds.min_total_queries:
            subjects = {v for e in events for v in e.values}
            if len(subjects) >= bounds.min_distinct_subjects:
                return ExtractionFinding(
                    signal=ExtractionSignal.BREADTH,
                    tenant_id=tenant_id,
                    principal_id=principal_id,
                    queries=len(events),
                    distinct_values=len(subjects),
                    window_seconds=bounds.window_seconds,
                    template="",
                    reason=(
                        f"Abnormal subject breadth refused: this principal touched "
                        f"{len(subjects)} distinct identifiers across {len(events)} "
                        f"queries in the last {int(bounds.window_seconds)} seconds, "
                        "with the phrasing varying between them. That is the same "
                        "sweep as systematic enumeration with the query shape rotated "
                        "to avoid matching (MITRE ATLAS AML.T0024). It clears on its "
                        "own once the principal stops for a full window."
                    ),
                )
        return None
