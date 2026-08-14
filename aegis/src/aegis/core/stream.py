r"""AegisEmitter — the one AG-UI streaming primitive every module emits through.

Wraps the AG-UI ``EventEncoder`` and event models. Modules call ergonomic à la carte
helpers; the emitter owns the wire rules (camelCase via the encoder, ``data: …\n\n``
framing, START→CONTENT→END bracketing, RUN_STARTED-first ordering). No module constructs
raw AG-UI events, and no module is required to use every helper.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from ag_ui.core import (
    CustomEvent,
    EventType,
    RunErrorEvent,
    RunFinishedEvent,
    RunStartedEvent,
    StepFinishedEvent,
    StepStartedEvent,
    TextMessageContentEvent,
    TextMessageEndEvent,
    TextMessageStartEvent,
    ToolCallArgsEvent,
    ToolCallEndEvent,
    ToolCallResultEvent,
    ToolCallStartEvent,
)
from ag_ui.encoder import EventEncoder

from aegis.core import stream_names
from aegis.core.events import SpanKind

Sink = Callable[[str], Awaitable[None]]


class _StepScope:
    """Async context manager that brackets a step with STEP_STARTED/STEP_FINISHED.

    The step's OpenInference ``span_kind`` is carried on both frames via AG-UI's
    ``raw_event`` passthrough. It used to be stored and never read — inert at every
    call site, so the trace could not tell a RETRIEVER step from a GUARDRAIL one
    despite every caller declaring it.
    """

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

    @property
    def span_kind(self) -> SpanKind:
        """The OpenInference span kind this step is acting as."""
        return self._span_kind

    def _raw(self) -> dict[str, str]:
        """The passthrough payload carrying the span kind onto the wire."""
        return {"spanKind": self._span_kind.value}

    async def __aenter__(self) -> _StepScope:
        """Enter the async context and emit STEP_STARTED.

        Returns:
            Self for use in 'as' clause.
        """
        await self._em._emit(
            StepStartedEvent(
                type=EventType.STEP_STARTED,
                step_name=self._name,
                raw_event=self._raw(),
            )
        )
        return self

    async def __aexit__(self, *exc: object) -> None:
        """Exit the async context and emit STEP_FINISHED.

        Args:
            exc: Exception info (unused).
        """
        await self._em._emit(
            StepFinishedEvent(
                type=EventType.STEP_FINISHED,
                step_name=self._name,
                raw_event=self._raw(),
            )
        )


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
        self._open_text: set[str] = set()
        self._open_tool: set[str] = set()

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

    async def reasoning(self, delta: str, *, message_id: str = "reasoning") -> None:
        """Stream one delta of live agent thinking (CustomEvent name='reasoning').

        Args:
            delta: Text delta representing thinking.
            message_id: Identifier for this reasoning stream (default: "reasoning").
        """
        await self._emit(
            CustomEvent(
                type=EventType.CUSTOM,
                name=stream_names.REASONING,
                value={"messageId": message_id, "delta": delta},
            )
        )

    async def text_start(self, message_id: str, role: str = "assistant") -> None:
        """Begin a bracketed assistant text message.

        Args:
            message_id: Identifier for this text message.
            role: Role of the message sender (default: "assistant").
        """
        self._open_text.add(message_id)
        await self._emit(
            TextMessageStartEvent(
                type=EventType.TEXT_MESSAGE_START, message_id=message_id, role=role
            )
        )

    async def text_delta(self, message_id: str, delta: str) -> None:
        """Append a text delta to an open message.

        Args:
            message_id: Identifier for this text message.
            delta: Text delta to append.

        Raises:
            RuntimeError: if ``message_id`` was not started.
        """
        if message_id not in self._open_text:
            raise RuntimeError(f"text_delta for message {message_id!r} not started")
        await self._emit(
            TextMessageContentEvent(
                type=EventType.TEXT_MESSAGE_CONTENT, message_id=message_id, delta=delta
            )
        )

    async def text_end(self, message_id: str) -> None:
        """End a bracketed assistant text message.

        Args:
            message_id: Identifier for this text message.

        Raises:
            RuntimeError: if ``message_id`` was not started.
        """
        if message_id not in self._open_text:
            raise RuntimeError(f"text_end for message {message_id!r} not started")
        self._open_text.discard(message_id)
        await self._emit(
            TextMessageEndEvent(type=EventType.TEXT_MESSAGE_END, message_id=message_id)
        )

    async def tool_start(self, tool_call_id: str, name: str) -> None:
        """Begin a bracketed tool call.

        Args:
            tool_call_id: Unique identifier for this tool call.
            name: Name of the tool being called.
        """
        self._open_tool.add(tool_call_id)
        await self._emit(
            ToolCallStartEvent(
                type=EventType.TOOL_CALL_START,
                tool_call_id=tool_call_id,
                tool_call_name=name,
            )
        )

    async def tool_args(self, tool_call_id: str, delta: str) -> None:
        """Append a partial-JSON args delta to an open tool call.

        Args:
            tool_call_id: Identifier of the tool call.
            delta: Partial JSON string representing the tool arguments.

        Raises:
            RuntimeError: if ``tool_call_id`` was not started.
        """
        if tool_call_id not in self._open_tool:
            raise RuntimeError(f"tool_args for {tool_call_id!r} not started")
        await self._emit(
            ToolCallArgsEvent(
                type=EventType.TOOL_CALL_ARGS,
                tool_call_id=tool_call_id,
                delta=delta,
            )
        )

    async def tool_end(self, tool_call_id: str) -> None:
        """End a bracketed tool call.

        Args:
            tool_call_id: Identifier of the tool call.

        Raises:
            RuntimeError: if ``tool_call_id`` was not started.
        """
        if tool_call_id not in self._open_tool:
            raise RuntimeError(f"tool_end for {tool_call_id!r} not started")
        self._open_tool.discard(tool_call_id)
        await self._emit(ToolCallEndEvent(type=EventType.TOOL_CALL_END, tool_call_id=tool_call_id))

    async def tool_result(self, tool_call_id: str, message_id: str, content: str) -> None:
        """Emit a tool result.

        Args:
            tool_call_id: Identifier of the tool call.
            message_id: Identifier of the message containing the result.
            content: Result content (string; JSON-encode structured output first).
        """
        await self._emit(
            ToolCallResultEvent(
                type=EventType.TOOL_CALL_RESULT,
                message_id=message_id,
                tool_call_id=tool_call_id,
                content=content,
            )
        )

    async def custom(self, name: str, value: dict) -> None:
        """Emit a domain CustomEvent.

        Args:
            name: The name of the custom event (must be in stream_names.ALL).
            value: Dictionary value for the custom event.

        Raises:
            ValueError: if ``name`` is not in :data:`aegis.core.stream_names.ALL`.
        """
        if not stream_names.is_known(name):
            msg = f"unknown CustomEvent name {name!r}; add it to aegis.core.stream_names"
            raise ValueError(msg)
        await self._emit(CustomEvent(type=EventType.CUSTOM, name=name, value=value))
