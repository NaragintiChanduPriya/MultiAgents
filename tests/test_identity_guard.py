"""Tests for the tool-level identity binding.

This is an access control, not a convenience: it is what stops a patient
typing somebody else's ID into the chat and being shown their records. It is
tested as such — the question in each case is "whose records would this tool
actually read".
"""

from __future__ import annotations

from agents.identity_guard import (
    IDENTITY_ARGUMENTS,
    bind_verified_identity,
    is_patient_identity,
)


class FakeState(dict):
    """Stands in for the ADK session state, which exposes .get()."""


class FakeContext:
    def __init__(self, state: dict | None):
        self.state = FakeState(state or {})


class FakeTool:
    name = "get_patient_appointments"


PATIENT = "PAT-20260101-0001"
OTHER_PATIENT = "PAT-20260101-0999"


# ══════════════════════════════════════════════════════════════════════════════
# Which identities may be substituted
# ══════════════════════════════════════════════════════════════════════════════


def test_recognises_a_patient_identity():
    """A patient chart identity is substitutable."""
    assert is_patient_identity("PAT-20260101-0001") is True
    assert is_patient_identity("pat-20260101-0001") is True
    assert is_patient_identity("PAT0001") is True


def test_rejects_staff_and_guest_identities():
    """Staff and guests do not name a chart, so nothing is substituted for them."""
    assert is_patient_identity("staff:physician:abc123") is False
    assert is_patient_identity("guest_visitor") is False
    assert is_patient_identity("") is False
    assert is_patient_identity(None) is False


def test_rejects_a_lookalike_identity():
    """A value that merely starts with the letters PAT is not a patient id."""
    assert is_patient_identity("PATRICIA") is False
    assert is_patient_identity("PATIENT") is False


# ══════════════════════════════════════════════════════════════════════════════
# Binding
# ══════════════════════════════════════════════════════════════════════════════


def test_fills_in_the_verified_identity_when_the_model_omits_it():
    """The model never has to know the identity; it is supplied."""
    args = {"authenticated_patient_id": ""}
    bind_verified_identity(FakeTool(), args, FakeContext({"patient_id": PATIENT}))
    assert args["authenticated_patient_id"] == PATIENT


def test_overrides_another_patients_id():
    """The central case: a different patient's id is discarded, not honoured."""
    args = {"authenticated_patient_id": OTHER_PATIENT}
    bind_verified_identity(FakeTool(), args, FakeContext({"patient_id": PATIENT}))
    assert args["authenticated_patient_id"] == PATIENT


def test_overrides_the_patient_id_argument_too():
    """Booking tools name the argument `patient_id`; it is bound the same way."""
    args = {"patient_id": OTHER_PATIENT, "doctor_id": "abc", "slot_id": "xyz"}
    bind_verified_identity(FakeTool(), args, FakeContext({"patient_id": PATIENT}))
    assert args["patient_id"] == PATIENT
    # Everything else is left exactly as the model chose it.
    assert args["doctor_id"] == "abc"
    assert args["slot_id"] == "xyz"


def test_leaves_unrelated_arguments_alone():
    """A tool with no identity argument is untouched."""
    args = {"specialty": "Cardiology"}
    bind_verified_identity(FakeTool(), args, FakeContext({"patient_id": PATIENT}))
    assert args == {"specialty": "Cardiology"}


def test_reads_the_identity_from_the_user_id_key():
    """The medication and discharge services store it under `user_id`."""
    args = {"authenticated_patient_id": OTHER_PATIENT}
    bind_verified_identity(FakeTool(), args, FakeContext({"user_id": PATIENT}))
    assert args["authenticated_patient_id"] == PATIENT


def test_prefers_patient_id_over_user_id():
    """When both are present, the explicit patient identity wins."""
    args = {"authenticated_patient_id": ""}
    bind_verified_identity(
        FakeTool(), args, FakeContext({"patient_id": PATIENT, "user_id": OTHER_PATIENT})
    )
    assert args["authenticated_patient_id"] == PATIENT


# ══════════════════════════════════════════════════════════════════════════════
# When binding must not happen
# ══════════════════════════════════════════════════════════════════════════════


def test_does_not_substitute_a_staff_token_into_a_patient_argument():
    """A clinician acting on a named patient must keep that patient's id."""
    args = {"authenticated_patient_id": PATIENT}
    bind_verified_identity(
        FakeTool(), args, FakeContext({"user_id": "staff:physician:abc123"})
    )
    assert args["authenticated_patient_id"] == PATIENT


def test_does_not_substitute_for_a_guest():
    """A guest has no chart, so nothing is bound."""
    args = {"authenticated_patient_id": "whatever"}
    bind_verified_identity(FakeTool(), args, FakeContext({"user_id": "guest_visitor"}))
    assert args["authenticated_patient_id"] == "whatever"


def test_handles_a_missing_state():
    """A context with no state must not raise — it simply binds nothing."""
    args = {"authenticated_patient_id": OTHER_PATIENT}
    bind_verified_identity(FakeTool(), args, FakeContext(None))
    assert args["authenticated_patient_id"] == OTHER_PATIENT

    class NoState:
        pass

    bind_verified_identity(FakeTool(), args, NoState())
    assert args["authenticated_patient_id"] == OTHER_PATIENT


def test_returns_none_so_the_tool_still_runs():
    """Returning a dict would short-circuit the tool; the guard must not."""
    args = {"authenticated_patient_id": ""}
    result = bind_verified_identity(
        FakeTool(), args, FakeContext({"patient_id": PATIENT})
    )
    assert result is None


# ══════════════════════════════════════════════════════════════════════════════
# The guard is actually attached
# ══════════════════════════════════════════════════════════════════════════════


def test_every_patient_scoped_agent_has_the_guard_attached():
    """A guard that is written but not wired protects nothing."""
    from agents.appointment_agent import appointment_agent
    from agents.discharge_summary_report_agent import discharge_summary_report_agent
    from agents.medication_agent import medication_agent

    for agent in (appointment_agent, medication_agent, discharge_summary_report_agent):
        assert agent.before_tool_callback is not None, f"{agent.name} has no guard"


def test_identity_arguments_match_the_real_tool_signatures():
    """If a tool renames its identity argument, this test fails rather than the guard silently missing it."""
    import inspect

    from agents.tools.query_data import get_appointment_details, get_patient_appointments
    from agents.tools.write_data import cancel_appointment, create_appointment

    for fn in (
        get_appointment_details,
        get_patient_appointments,
        cancel_appointment,
        create_appointment,
    ):
        params = set(inspect.signature(fn).parameters)
        assert params & IDENTITY_ARGUMENTS, (
            f"{fn.__name__} has no argument the identity guard recognises: {params}"
        )
