"""Rate-limit handling shared by every agent service.

Groq's free tier allows 8,000 tokens per minute. A single appointment-agent
turn carries ten tool schemas and a long instruction, so it costs roughly
4,000 tokens — two turns and the window is spent. Without this, an ordinary
second question returns "I encountered an error", which reads like a bug and
tells the user nothing they can act on.

So: run the agent, and when the provider says "retry in N seconds", wait that
long and try once more. If it still fails, say plainly that the assistant is
busy and roughly when to try again.
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any, AsyncIterator, Callable, List, Optional

logger = logging.getLogger(__name__)

# How long the provider asks us to wait, e.g. "Please try again in 15.04s".
_RETRY_HINT_RE = re.compile(r"try again in\s+([0-9.]+)\s*s", re.IGNORECASE)

DEFAULT_MAX_ATTEMPTS = 3
MAX_SLEEP_SECONDS = 30.0


def is_rate_limit_error(error: BaseException) -> bool:
    """True when the failure is a provider throttle rather than a real fault."""
    name = type(error).__name__.lower()
    if "ratelimit" in name:
        return True
    text = str(error).lower()
    return "rate_limit" in text or "rate limit" in text or "429" in text


def retry_after_seconds(error: BaseException, default: float = 10.0) -> float:
    """Extracts the provider's suggested wait, clamped to something sane."""
    match = _RETRY_HINT_RE.search(str(error))
    if match:
        try:
            # A small cushion: retrying on the exact boundary tends to fail again.
            return min(float(match.group(1)) + 1.0, MAX_SLEEP_SECONDS)
        except ValueError:
            pass
    return min(default, MAX_SLEEP_SECONDS)


def rate_limit_message(error: BaseException, assistant: str = "assistant") -> str:
    """A patient-facing explanation of a throttle, with a concrete wait."""
    wait = int(round(retry_after_seconds(error)))
    return (
        f"The {assistant} is handling a lot of requests right now and has hit its "
        f"usage limit for the minute. Please try again in about {wait} seconds — "
        "your request was not lost, and nothing has been booked or changed."
    )


async def collect_agent_text(
    run: Callable[[], AsyncIterator[Any]],
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
) -> str:
    """Runs an ADK agent stream and returns its concatenated text output.

    `run` must be a zero-argument callable returning a fresh async iterator,
    because a stream cannot be replayed: each retry needs a new one.

    Raises the last exception if every attempt fails.
    """
    last_error: Optional[BaseException] = None

    for attempt in range(1, max_attempts + 1):
        chunks: List[str] = []
        try:
            async for event in run():
                content = getattr(event, "content", None)
                if content and getattr(content, "parts", None):
                    for part in content.parts:
                        text = getattr(part, "text", None)
                        if text:
                            chunks.append(text)
            return "".join(chunks).strip()

        except Exception as error:  # noqa: BLE001 - re-raised below
            last_error = error
            if attempt >= max_attempts or not is_rate_limit_error(error):
                raise

            wait = retry_after_seconds(error)
            logger.warning(
                "Provider rate limit hit (attempt %d/%d); retrying in %.1fs",
                attempt,
                max_attempts,
                wait,
            )
            await asyncio.sleep(wait)

    # Unreachable: the loop either returns or raises.
    raise last_error if last_error else RuntimeError("Agent produced no result.")
