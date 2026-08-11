import json

from ag_ui.core import (
    CustomEvent,
    RunFinishedEvent,
    RunStartedEvent,
    StepFinishedEvent,
    StepStartedEvent,
)

from aegis.core import stream_names
from aegis.core.events import SpanKind
from aegis.core.stream import AegisEmitter


class CaptureSink:
    def __init__(self):
        self.frames = []

    async def __call__(self, frame):
        self.frames.append(frame)


_MODEL = {
    "RUN_STARTED": RunStartedEvent,
    "RUN_FINISHED": RunFinishedEvent,
    "STEP_STARTED": StepStartedEvent,
    "STEP_FINISHED": StepFinishedEvent,
    "CUSTOM": CustomEvent,
}


async def test_frames_are_valid_agui():
    sink = CaptureSink()
    em = AegisEmitter(thread_id="t", run_id="r", sink=sink)
    await em.run_started()
    async with em.step("guard_input", SpanKind.GUARDRAIL):
        await em.custom(stream_names.GUARDRAIL_VERDICT, {"verdict": "block"})
    await em.run_finished()
    payloads = [json.loads(f[len("data: "):].strip()) for f in sink.frames]
    assert payloads[0]["type"] == "RUN_STARTED" and payloads[-1]["type"] == "RUN_FINISHED"
    for p in payloads:  # every frame re-validates through its ag_ui model
        model = _MODEL[p["type"]]
        model.model_validate(p)  # raises if not spec-valid
