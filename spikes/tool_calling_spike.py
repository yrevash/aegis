"""
THE critical de-risk spike — tool/function-calling through the TCS GenAI Lab gateway.

See docs/hackathon/brief.md §8 and docs/architecture/backend.md §9. Basic chat is already confirmed
working; this tests the assumption the whole agent design depends on: does passing a
`tools` parameter yield a `tool_calls` block back?

  - If YES  -> build the LangGraph agent loop normally.
  - If NO   -> pivot to a ReAct / structured-output (JSON) fallback.

Run:  python spikes/tool_calling_spike.py
(Uses the exact langchain_openai setup already confirmed to connect.)
"""

import httpx
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI


@tool
def get_weather(city: str) -> str:
    """Get the current weather for a given city."""
    return f"It is sunny and 31C in {city}."


llm = ChatOpenAI(
    base_url="https://genailab.tcs.in",
    model="genailab-maas-gpt-5.4-mini",
    api_key="PASTE_YOUR_KEY_HERE",          # same key that worked for the basic call
    http_client=httpx.Client(verify=False),  # self-signed cert on the gateway
)

response = llm.bind_tools([get_weather]).invoke(
    "What's the weather in Mumbai right now?"
)

print("content:   ", repr(response.content))
print("tool_calls:", response.tool_calls)

if response.tool_calls:
    print("\n✅ TOOL-CALLING WORKS -> build the LangGraph agent normally.")
    for tc in response.tool_calls:
        print(f"   -> {tc['name']}({tc['args']})")
else:
    print("\n❌ No tool_calls returned.")
    print("   -> Pivot to ReAct / structured-output fallback (see docs/architecture/backend.md §9).")
