"""Adapter: an Aegis :class:`ChatCompleter` presented to NeMo as its ``main`` LLM.

**The gap this closes.** ``LLMRails(config)`` was constructed with no ``llm``
argument, so NeMo fell back to instantiating the ``models:`` block in
``config/config.yml`` — an OpenAI-shaped client pointed at a lab endpoint, with
its own key, its own base URL and no relationship to the cost-routed gateway the
rest of the platform calls. The Colang custom *actions* were properly wired (they
read the host completer via :func:`aegis.guardrails.nemo.set_completer`), but the
engine's own model was not. Anything that reached it — a NeMo-native self-check
rail, a dialog rail, an LLM-generated bot message — would have called a different
model than the one the operator configured, or failed outright.

The fix is this thin LangChain :class:`BaseChatModel` shim: NeMo speaks LangChain,
Aegis speaks ``ChatCompleter``, and the translation is a dozen lines. One model,
one budget, one place to route.

``langchain_core`` ships as a hard dependency of ``nemoguardrails``, so it is
reached through :func:`aegis.core.lazy.require` under the same ``aegis[nemo]``
extra — importing :mod:`aegis.guardrails.nemo` still needs neither.
"""

from __future__ import annotations

from typing import Any

from aegis.core.lazy import require

#: LangChain message-class name → OpenAI chat role.
_ROLE_BY_CLASS = {
    "SystemMessage": "system",
    "HumanMessage": "user",
    "AIMessage": "assistant",
    "ToolMessage": "tool",
    "FunctionMessage": "function",
    "ChatMessage": "user",
}


def _role_of(message: Any) -> str:  # noqa: ANN401 - a LangChain BaseMessage
    """Map a LangChain message to its OpenAI chat role."""
    explicit = getattr(message, "role", None)
    if isinstance(explicit, str) and explicit:
        return explicit
    return _ROLE_BY_CLASS.get(type(message).__name__, "user")


def chat_model_from_completer(completer: Any) -> Any:  # noqa: ANN401 - ChatCompleter in, LLM out
    """Wrap a :class:`~aegis.core.interfaces.ChatCompleter` as a LangChain chat model.

    Args:
        completer: The async chat-completion callable the host wired in.

    Returns:
        A ``BaseChatModel`` instance suitable for ``LLMRails(config, llm=...)``.

    Raises:
        ImportError: If the ``aegis[nemo]`` extra (and its ``langchain_core``) is
            not installed.
    """
    chat_models = require("aegis[nemo]", "langchain_core.language_models.chat_models")
    outputs = require("aegis[nemo]", "langchain_core.outputs")

    class CompleterChatModel(chat_models.BaseChatModel):  # type: ignore[misc, valid-type]
        """A LangChain chat model backed by an injected Aegis ``ChatCompleter``."""

        aegis_completer: Any = None
        #: NeMo sets this on the model it is handed; guardrail calls want determinism.
        temperature: float = 0.0

        @property
        def _llm_type(self) -> str:
            """LangChain's model-type tag, used in its callback/trace metadata."""
            return "aegis-chat-completer"

        def _generate(self, messages: list, stop: list | None = None, **kwargs: Any) -> Any:  # noqa: ANN401
            """Refuse the synchronous path loudly rather than blocking an event loop.

            Every Aegis completer is async by contract. Bridging with
            ``asyncio.run`` here would deadlock inside a running loop — which is
            where NeMo always executes — so this says so instead of hanging.
            """
            raise NotImplementedError(
                "aegis ChatCompleter is async-only; NeMo Guardrails must use the "
                "async generation path (generate_async)."
            )

        async def _agenerate(
            self, messages: list, stop: list | None = None, **kwargs: Any  # noqa: ANN401, ARG002
        ) -> Any:  # noqa: ANN401 - langchain ChatResult
            """Translate LangChain messages → the completer → a LangChain result."""
            payload = [
                {"role": _role_of(message), "content": message.content} for message in messages
            ]
            text = await self.aegis_completer(payload)
            message = outputs.ChatGeneration(
                message=_ai_message(text),
            )
            return outputs.ChatResult(generations=[message])

    def _ai_message(text: str) -> Any:  # noqa: ANN401 - langchain AIMessage
        """Build a LangChain ``AIMessage`` (imported lazily with everything else)."""
        messages_module = require("aegis[nemo]", "langchain_core.messages")
        return messages_module.AIMessage(content=text)

    return CompleterChatModel(aegis_completer=completer)
