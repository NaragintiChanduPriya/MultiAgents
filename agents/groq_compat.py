"""Groq/LiteLLM compatibility shims, applied once for every agent.

Two problems this fixes, both specific to Groq's reasoning models
(`openai/gpt-oss-*`, `qwen3-*`, `deepseek-r1-*`):

1. **Reasoning leaking into the answer.** By default Groq returns the model's
   chain of thought in the same `content` field as the answer, so a patient
   sees "We need to answer: ... Provide empathetic tone." before the reply.
   Setting `reasoning_format: "hidden"` makes Groq drop it server-side. That
   is far more reliable than trying to spot the boundary with a regex.

2. **`reasoning_content` rejected on replay.** When a multi-turn history is
   sent back, Groq 400s on the `reasoning_content` key it produced itself, so
   it is stripped from outgoing messages.

Import `apply_groq_compatibility_patches()` and call it before building an
agent. It is idempotent.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_PATCHED = False

# Models that emit a separate reasoning channel.
_REASONING_MODEL_HINTS = ("gpt-oss", "qwen3", "deepseek-r1", "-r1-")


def _is_reasoning_model(model: object) -> bool:
    """True when the target model produces chain-of-thought output."""
    name = str(model or "").lower()
    return any(hint in name for hint in _REASONING_MODEL_HINTS)


def apply_groq_compatibility_patches() -> bool:
    """Patches LiteLLM's Groq transform. Returns True when the patch is active.

    Safe to call repeatedly and safe to call when LiteLLM's internals have
    moved: a failure here degrades output quality, it must never stop the
    service from starting.
    """
    global _PATCHED
    if _PATCHED:
        return True

    try:
        from litellm.llms.groq.chat.transformation import GroqChatConfig
    except Exception as exc:  # pragma: no cover - depends on litellm internals
        logger.warning("Groq compatibility patch not applied (import failed): %s", exc)
        return False

    original_transform_request = GroqChatConfig.transform_request

    def patched_transform_request(
        self, model, messages, optional_params, litellm_params, headers
    ):
        result = original_transform_request(
            self, model, messages, optional_params, litellm_params, headers
        )

        if isinstance(result, dict):
            # 1. Strip reasoning_content Groq will not accept back.
            if isinstance(result.get("messages"), list):
                for message in result["messages"]:
                    if isinstance(message, dict):
                        message.pop("reasoning_content", None)

            # 2. Keep the model's thinking out of the answer text.
            if _is_reasoning_model(result.get("model", model)):
                result.setdefault("reasoning_format", "hidden")

        return result

    GroqChatConfig.transform_request = patched_transform_request
    _PATCHED = True
    logger.info("Groq compatibility patches applied (reasoning hidden, history sanitized).")
    return True
