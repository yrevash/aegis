// AG-UI event shape (subset we consume); @ag-ui/core provides full types/zod schemas.
export interface AguiEvent {
  type: string;
  name?: string;
  value?: any;
  [k: string]: unknown;
}

/** Split an AG-UI SSE stream into decoded events (frames are `data: {json}\n\n`). */
export function decodeAguiStream(text: string): AguiEvent[] {
  return text
    .split("\n\n")
    .map((f) => f.trim())
    .filter((f) => f.startsWith("data:"))
    .map((f) => JSON.parse(f.slice(f.indexOf("data:") + 5).trim()) as AguiEvent);
}
