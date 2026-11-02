"""Tests for the shared provider rate-limit handling."""

from __future__ import annotations

import pytest

from services.llm_retry import (
    collect_agent_text,
    is_rate_limit_error,
    rate_limit_message,
    retry_after_seconds,
)

GROQ_429 = (
    "litellm.RateLimitError: RateLimitError: GroqException - "
    '{"error":{"message":"Rate limit reached for model `openai/gpt-oss-120b` '
    "on tokens per minute (TPM): Limit 8000, Used 5958, Requested 4083. "
    'Please try again in 15.3075s.","type":"tokens","code":"rate_limit_exceeded"}}'
)


class FakeRateLimitError(Exception):
    """Stands in for litellm.RateLimitError, matched by class name."""


class _Part:
    def __init__(self, text):
        self.text = text


class _Content:
    def __init__(self, parts):
        self.parts = parts


class _Event:
    def __init__(self, text):
        self.content = _Content([_Part(text)])


# ══════════════════════════════════════════════════════════════════════════════
# Detection
# ══════════════════════════════════════════════════════════════════════════════


def test_detects_a_rate_limit_by_class_name():
    """A litellm RateLimitError is recognised from its type name."""
    assert is_rate_limit_error(FakeRateLimitError("boom")) is True


def test_detects_a_rate_limit_by_message():
    """A throttle is recognised from the provider's message text."""
    assert is_rate_limit_error(RuntimeError(GROQ_429)) is True
    assert is_rate_limit_error(RuntimeError("HTTP 429 Too Many Requests")) is True


def test_does_not_mistake_other_errors_for_throttles():
    """An ordinary failure must not be retried as if it were a throttle."""
    assert is_rate_limit_error(ValueError("bad slot id")) is False
    assert is_rate_limit_error(RuntimeError("connection reset")) is False


# ══════════════════════════════════════════════════════════════════════════════
# Backoff
# ══════════════════════════════════════════════════════════════════════════════


def test_reads_the_providers_suggested_wait():
    """The 'try again in Ns' hint drives the backoff, plus a small cushion."""
    assert retry_after_seconds(RuntimeError(GROQ_429)) == pytest.approx(16.3075)


def test_falls_back_when_no_hint_is_given():
    """Without a hint, a sane default is used."""
    assert retry_after_seconds(RuntimeError("429"), default=7.0) == 7.0


def test_clamps_an_absurd_wait():
    """A huge suggested wait is capped so a request cannot hang indefinitely."""
    assert retry_after_seconds(RuntimeError("Please try again in 900s")) == 30.0


def test_rate_limit_message_is_actionable():
    """The patient-facing message states the wait and reassures about state."""
    message = rate_limit_message(RuntimeError(GROQ_429), "appointment assistant")
    assert "appointment assistant" in message
    assert "16 seconds" in message
    assert "nothing has been booked" in message


# ══════════════════════════════════════════════════════════════════════════════
# Retrying the agent stream
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_collects_text_from_the_stream():
    """Text parts across events are concatenated."""

    async def run():
        yield _Event("Hello ")
        yield _Event("world.")

    assert await collect_agent_text(lambda: run()) == "Hello world."


@pytest.mark.asyncio
async def test_ignores_events_without_text():
    """Tool-call events carrying no text do not break collection."""

    class _Empty:
        content = None

    async def run():
        yield _Empty()
        yield _Event("Answer.")

    assert await collect_agent_text(lambda: run()) == "Answer."


@pytest.mark.asyncio
async def test_retries_through_a_throttle(monkeypatch):
    """A throttled first attempt is retried and the second attempt's text wins."""
    slept = []

    async def fake_sleep(seconds):
        slept.append(seconds)

    monkeypatch.setattr("services.llm_retry.asyncio.sleep", fake_sleep)

    attempts = {"n": 0}

    async def run():
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise FakeRateLimitError(GROQ_429)
            yield  # pragma: no cover - makes this an async generator
        yield _Event("Recovered.")

    assert await collect_agent_text(lambda: run()) == "Recovered."
    assert attempts["n"] == 2
    assert slept == [pytest.approx(16.3075)]


@pytest.mark.asyncio
async def test_gives_up_after_the_attempt_budget(monkeypatch):
    """Persistent throttling raises, so the caller can explain it to the user."""

    async def fake_sleep(_seconds):
        return None

    monkeypatch.setattr("services.llm_retry.asyncio.sleep", fake_sleep)

    async def run():
        raise FakeRateLimitError(GROQ_429)
        yield  # pragma: no cover

    with pytest.raises(FakeRateLimitError):
        await collect_agent_text(lambda: run(), max_attempts=2)


@pytest.mark.asyncio
async def test_does_not_retry_a_real_failure(monkeypatch):
    """A genuine error is raised immediately rather than retried three times."""
    attempts = {"n": 0}

    async def run():
        attempts["n"] += 1
        raise ValueError("bad slot id")
        yield  # pragma: no cover

    with pytest.raises(ValueError):
        await collect_agent_text(lambda: run())
    assert attempts["n"] == 1
