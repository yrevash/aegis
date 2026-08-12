import { describe, it, expect } from "vitest";
import { decodeAguiStream } from "./decode";
import { STREAM_NAMES } from "./streamNames";

const FIXTURE =
  'data: {"type":"RUN_STARTED","threadId":"t","runId":"r"}\n\n' +
  'data: {"type":"STEP_STARTED","stepName":"guard_input"}\n\n' +
  `data: {"type":"CUSTOM","name":"${STREAM_NAMES.GUARDRAIL_VERDICT}","value":{"verdict":"block","rules":["injection"],"rationale":"matched signature"}}\n\n` +
  `data: {"type":"CUSTOM","name":"${STREAM_NAMES.REASONING}","value":{"messageId":"reasoning","delta":"checking policy"}}\n\n` +
  'data: {"type":"STEP_FINISHED","stepName":"guard_input"}\n\n' +
  'data: {"type":"RUN_FINISHED","threadId":"t","runId":"r"}\n\n';

describe("decodeAguiStream", () => {
  it("decodes frames and routes custom payloads by name", () => {
    const events = decodeAguiStream(FIXTURE);
    expect(events[0].type).toBe("RUN_STARTED");
    expect(events[events.length - 1].type).toBe("RUN_FINISHED");
    const verdict = events.find((e) => e.type === "CUSTOM" && e.name === STREAM_NAMES.GUARDRAIL_VERDICT);
    expect(verdict?.value.verdict).toBe("block");
    const reasoning = events.find((e) => e.type === "CUSTOM" && e.name === STREAM_NAMES.REASONING);
    expect(reasoning?.value.delta).toBe("checking policy");
  });
});
