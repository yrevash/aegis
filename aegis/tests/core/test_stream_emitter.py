"""Test AegisEmitter lifecycle and AG-UI event encoding."""

import json

import pytest

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
