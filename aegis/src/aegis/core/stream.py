r"""AegisEmitter — the one AG-UI streaming primitive every module emits through.

Wraps the AG-UI ``EventEncoder`` and event models. Modules call ergonomic à la carte
helpers; the emitter owns the wire rules (camelCase via the encoder, ``data: …\n\n``
framing, START→CONTENT→END bracketing, RUN_STARTED-first ordering). No module constructs
raw AG-UI events, and no module is required to use every helper.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from ag_ui.core import (
    EventType,
    RunErrorEvent,
    RunFinishedEvent,
    RunStartedEvent,
    StepFinishedEvent,
    StepStartedEvent,
)
from ag_ui.encoder import EventEncoder

from aegis.core.events import SpanKind

Sink = Callable[[str], Awaitable[None]]


class _StepScope:
    """Async context manager that brackets a step with STEP_STARTED/STEP_FINISHED."""

    def __init__(self, emitter: AegisEmitter, name: str, span_kind: SpanKind) -> None:
        """Initialize the step scope.

        Args:
            emitter: The parent AegisEmitter instance.
            name: Step name (e.g., 'guard_input').
            span_kind: OpenInference span kind for this step.
        """
        self._em = emitter
        self._name = name
        self._span_kind = span_kind

    async def __aenter__(self) -> _StepScope:
        """Enter the async context and emit STEP_STARTED.

        Returns:
            Self for use in 'as' clause.
        """
        await self._em._emit(StepStartedEvent(type=EventType.STEP_STARTED, step_name=self._name))
        return self

    async def __aexit__(self, *exc: object) -> None:
        """Exit the async context and emit STEP_FINISHED.

        Args:
            exc: Exception info (unused).
        """
        await self._em._emit(StepFinishedEvent(type=EventType.STEP_FINISHED, step_name=self._name))


class AegisEmitter:
    """Emit an AG-UI event stream for one run, à la carte, over an async ``sink``."""

    def __init__(self, *, thread_id: str, run_id: str, sink: Sink) -> None:
        """Create an emitter bound to one run.

        Args:
            thread_id: AG-UI thread id (conversation).
            run_id: AG-UI run id (this turn).
            sink: async callable receiving each encoded SSE frame string.
        """
        self._thread_id = thread_id
        self._run_id = run_id
        self._sink = sink
        self._encoder = EventEncoder()

    async def _emit(self, event: object) -> None:
        """Encode ``event`` to an SSE frame and hand it to the sink.

        Args:
            event: An AG-UI event object to encode and emit.
        """
        await self._sink(self._encoder.encode(event))

    async def run_started(self) -> None:
        """Emit RUN_STARTED (must be the first event of the run)."""
        await self._emit(
            RunStartedEvent(
                type=EventType.RUN_STARTED,
                thread_id=self._thread_id,
                run_id=self._run_id,
            )
        )

    async def run_finished(self, result: dict | None = None) -> None:
        """Emit RUN_FINISHED (the terminal event of a successful run).

        Args:
            result: Optional result dict to include in the event.
        """
        await self._emit(
            RunFinishedEvent(
                type=EventType.RUN_FINISHED,
                thread_id=self._thread_id,
                run_id=self._run_id,
                result=result,
            )
        )

    async def run_error(self, message: str, code: str | None = None) -> None:
        """Emit RUN_ERROR (terminal event of a failed run).

        Args:
            message: Error message text.
            code: Optional error code.
        """
        await self._emit(RunErrorEvent(type=EventType.RUN_ERROR, message=message, code=code))

    def step(self, name: str, span_kind: SpanKind) -> _StepScope:
        """Return an async context manager bracketing a step with STEP_STARTED/FINISHED.

        Args:
            name: Human-readable step name (e.g., 'guard_input').
            span_kind: OpenInference span kind for this step.

        Returns:
            An async context manager (_StepScope) that emits bracketing events.
        """
        return _StepScope(self, name, span_kind)
