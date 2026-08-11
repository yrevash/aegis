"""Test AegisEmitter lifecycle and AG-UI event encoding."""

import json

import pytest

from aegis.core import stream_names
from aegis.core.events import SpanKind
from aegis.core.stream import AegisEmitter


class CaptureSink:
    """Test helper that collects encoded event frames."""

    def __init__(self) -> None:
        """Initialize the capture sink."""
        self.frames: list[str] = []

    async def __call__(self, frame: str) -> None:
        """Append a frame to the captured list.

        Args:
            frame: An encoded SSE frame string.
        """
        self.frames.append(frame)


def _events(frames: list[str]) -> list[dict]:
    """Extract AG-UI events from SSE frames.

    Each frame is 'data: {json}\\n\\n'. No SSE event: line (AG-UI puts type in-band).

    Args:
        frames: List of SSE frame strings.

    Returns:
        List of decoded event dictionaries.

    Raises:
        AssertionError: If frame format is invalid.
    """
    out = []
    for f in frames:
        assert f.startswith("data: ") and f.endswith("\n\n"), f"Invalid frame format: {f!r}"
        assert "\nevent:" not in f, "AG-UI puts type in-band, no SSE event: line"
        out.append(json.loads(f[len("data: ") :].strip()))
    return out


@pytest.mark.asyncio
async def test_lifecycle_and_camelcase() -> None:
    """Test RUN_STARTED and RUN_FINISHED events with camelCase field names."""
    sink = CaptureSink()
    em = AegisEmitter(thread_id="t1", run_id="r1", sink=sink)
    await em.run_started()
    await em.run_finished({"ok": True})
    evs = _events(sink.frames)
    assert evs[0]["type"] == "RUN_STARTED"
    assert evs[0]["threadId"] == "t1"
    assert evs[0]["runId"] == "r1"
    assert evs[-1]["type"] == "RUN_FINISHED"


@pytest.mark.asyncio
async def test_step_brackets() -> None:
    """Test STEP_STARTED/STEP_FINISHED bracketing via async context manager."""
    sink = CaptureSink()
    em = AegisEmitter(thread_id="t1", run_id="r1", sink=sink)
    async with em.step("guard_input", SpanKind.GUARDRAIL):
        pass
    evs = _events(sink.frames)
    assert [e["type"] for e in evs] == ["STEP_STARTED", "STEP_FINISHED"]
    assert evs[0]["stepName"] == "guard_input"


@pytest.mark.asyncio
async def test_reasoning_is_custom_event() -> None:
    """Test reasoning() emits a CUSTOM event with stream_names.REASONING."""
    sink = CaptureSink()
    em = AegisEmitter(thread_id="t", run_id="r", sink=sink)
    await em.reasoning("thinking about the refund policy")
    ev = _events(sink.frames)[0]
    assert ev["type"] == "CUSTOM" and ev["name"] == stream_names.REASONING
    assert ev["value"]["delta"] == "thinking about the refund policy"


@pytest.mark.asyncio
async def test_text_bracketing_and_guard() -> None:
    """Test text_* bracketing and guard on delta/end without start."""
    sink = CaptureSink()
    em = AegisEmitter(thread_id="t", run_id="r", sink=sink)
    await em.text_start("m1")
    await em.text_delta("m1", "Hello ")
    await em.text_delta("m1", "world")
    await em.text_end("m1")
    types = [e["type"] for e in _events(sink.frames)]
    expected = [
        "TEXT_MESSAGE_START",
        "TEXT_MESSAGE_CONTENT",
        "TEXT_MESSAGE_CONTENT",
        "TEXT_MESSAGE_END",
    ]
    assert types == expected
    with pytest.raises(RuntimeError):
        await em.text_delta("never-started", "x")


@pytest.mark.asyncio
async def test_tool_bracketing() -> None:
    """Test tool_start/args/end/result event bracketing."""
    sink = CaptureSink()
    em = AegisEmitter(thread_id="t", run_id="r", sink=sink)
    await em.tool_start("tc1", "update_status")
    await em.tool_args("tc1", '{"id":')
    await em.tool_args("tc1", '"r1"}')
    await em.tool_end("tc1")
    await em.tool_result("tc1", "m2", "ok")
    types = [e["type"] for e in _events(sink.frames)]
    expected = [
        "TOOL_CALL_START",
        "TOOL_CALL_ARGS",
        "TOOL_CALL_ARGS",
        "TOOL_CALL_END",
        "TOOL_CALL_RESULT",
    ]
    assert types == expected


@pytest.mark.asyncio
async def test_custom_rejects_unknown_name() -> None:
    """Test custom() accepts known stream names and rejects unknown."""
    sink = CaptureSink()
    em = AegisEmitter(thread_id="t", run_id="r", sink=sink)
    await em.custom(stream_names.GUARDRAIL_VERDICT, {"verdict": "pass"})
    assert _events(sink.frames)[0]["name"] == stream_names.GUARDRAIL_VERDICT
    with pytest.raises(ValueError):
        await em.custom("not-registered", {})
