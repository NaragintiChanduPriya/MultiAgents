"""Prescription tool tests, run against an in-memory MongoDB.

The prescription is what the pharmacy hands over, so these tests care about
one thing above all: that what comes back is exactly what was prescribed, and
that nothing can be read for a patient the caller is not.
"""

from __future__ import annotations

from agents.tools.medication_tools import get_medication_by_record_id
from agents.tools.prescription_tools import (
    format_prescription_for_prompt,
    get_prescription_by_id,
    list_prescriptions_for_patient,
)
from tests.conftest import (
    PATIENT_2_HUMAN_ID,
    PATIENT_HUMAN_ID,
    PRESCRIPTION_HUMAN_ID,
    RX_ITEM_AMOXICILLIN,
    RX_ITEM_IBUPROFEN,
)


# ══════════════════════════════════════════════════════════════════════════════
# 1. Lookup and validation
# ══════════════════════════════════════════════════════════════════════════════


def test_requires_a_prescription_id():
    """A blank identifier is refused before any query runs."""
    result = get_prescription_by_id("")
    assert result["status"] == "error"
    assert result["found"] is False
    assert result["prescription"] is None


def test_handles_none():
    """A None identifier is a validation error, not a crash."""
    assert get_prescription_by_id(None)["status"] == "error"


def test_unknown_prescription(fake_db):
    """An identifier matching nothing reports not_found."""
    result = get_prescription_by_id("RX-19000101-0001")
    assert result["status"] == "not_found"
    assert result["found"] is False
    assert "RX-19000101-0001" in result["message"]


def test_is_case_insensitive(fake_db):
    """A lower-cased identifier still resolves."""
    result = get_prescription_by_id(PRESCRIPTION_HUMAN_ID.lower())
    assert result["status"] == "success"
    assert result["prescription"]["prescription_id"] == PRESCRIPTION_HUMAN_ID


# ══════════════════════════════════════════════════════════════════════════════
# 2. What comes back
# ══════════════════════════════════════════════════════════════════════════════


def test_returns_the_header(fake_db):
    """Status, prescriber, diagnosis and priority all come back."""
    rx = get_prescription_by_id(PRESCRIPTION_HUMAN_ID)["prescription"]
    assert rx["prescription_id"] == PRESCRIPTION_HUMAN_ID
    assert rx["status"] == "ready"
    assert rx["status_text"] == "Ready for collection"
    assert rx["prescriber"] == "Dr. Ananya Sharma"
    assert rx["diagnosis"] == "Lower respiratory tract infection"
    assert rx["item_count"] == 2


def test_returns_every_prescribed_item(fake_db):
    """Both medicines come back, with their dose and frequency intact."""
    items = get_prescription_by_id(PRESCRIPTION_HUMAN_ID)["items"]
    assert len(items) == 2

    amox = next(i for i in items if i["medication_name"] == "Amoxicillin")
    assert amox["strength"] == "500 mg"
    assert amox["strength_value"] == 500.0
    assert amox["strength_unit"] == "mg"
    assert amox["frequency"] == "three times a day"
    assert amox["route"] == "oral"
    assert amox["quantity"] == 21
    assert amox["instructions"] == "Take with food."


def test_renders_course_length_in_words(fake_db):
    """A duration becomes something a patient can read; 0 means ongoing."""
    items = get_prescription_by_id(PRESCRIPTION_HUMAN_ID)["items"]
    amox = next(i for i in items if i["medication_name"] == "Amoxicillin")
    ibu = next(i for i in items if i["medication_name"] == "Ibuprofen")
    assert amox["duration_text"] == "7 days"
    assert ibu["duration_text"] == "Ongoing until reviewed"


def test_surfaces_a_substitution(fake_db):
    """What the pharmacy actually handed over is carried through."""
    items = get_prescription_by_id(PRESCRIPTION_HUMAN_ID)["items"]
    ibu = next(i for i in items if i["medication_name"] == "Ibuprofen")
    assert ibu["substituted_with"] == "Brufen"
    assert ibu["pharmacy_note"] == "Generic supplied."


def test_returns_the_safety_flags(fake_db):
    """The pharmacist's warnings travel with the prescription."""
    flags = get_prescription_by_id(PRESCRIPTION_HUMAN_ID)["safety_flags"]
    assert len(flags) == 1
    assert flags[0]["severity"] == "high"
    assert flags[0]["drug_a"] == "Ibuprofen"
    assert flags[0]["drug_b"] == "Warfarin"
    assert flags[0]["acknowledged"] is False


def test_returns_the_patient_and_their_allergies(fake_db):
    """Counselling needs to know who it is for and what they react to."""
    patient = get_prescription_by_id(PRESCRIPTION_HUMAN_ID)["patient"]
    assert patient["patient_id"] == PATIENT_HUMAN_ID
    assert patient["first_name"] == "Senthil"
    assert patient["literacy_level"] == "basic"
    assert "Penicillin" in patient["allergies"]


def test_reports_a_database_failure(fake_db, monkeypatch):
    """A driver failure returns a sanitized error, never a stack trace."""
    import database as database_module

    def explode(*_args, **_kwargs):
        raise RuntimeError("connection reset")

    monkeypatch.setattr(database_module, "get_database", explode)
    result = get_prescription_by_id(PRESCRIPTION_HUMAN_ID)
    assert result["status"] == "error"
    assert "connection reset" in result["message"]


# ══════════════════════════════════════════════════════════════════════════════
# 3. Listing
# ══════════════════════════════════════════════════════════════════════════════


def test_lists_a_patients_prescriptions(fake_db):
    """A patient's prescriptions come back with the ids needed to read them."""
    result = list_prescriptions_for_patient(PATIENT_HUMAN_ID)
    assert result["status"] == "success"
    assert result["count"] == 1

    entry = result["prescriptions"][0]
    assert entry["prescription_id"] == PRESCRIPTION_HUMAN_ID
    assert entry["item_count"] == 2
    assert set(entry["medicines"]) == {"Amoxicillin", "Ibuprofen"}


def test_filters_by_status(fake_db):
    """A status filter narrows the list."""
    assert list_prescriptions_for_patient(PATIENT_HUMAN_ID, status="ready")["count"] == 1
    assert list_prescriptions_for_patient(PATIENT_HUMAN_ID, status="dispensed")["count"] == 0


def test_lists_nothing_for_a_patient_with_none(fake_db):
    """A patient with no prescriptions gets an empty list, not an error."""
    result = list_prescriptions_for_patient(PATIENT_2_HUMAN_ID)
    assert result["status"] == "success"
    assert result["count"] == 0


def test_listing_requires_a_patient_id():
    """A blank patient id is refused."""
    assert list_prescriptions_for_patient("")["status"] == "error"


def test_listing_unknown_patient(fake_db):
    """An unknown patient is reported as not found."""
    assert list_prescriptions_for_patient("PAT-19000101-0001")["status"] == "not_found"


# ══════════════════════════════════════════════════════════════════════════════
# 4. A prescribed line is also a medication record
# ══════════════════════════════════════════════════════════════════════════════


def test_a_prescribed_item_resolves_as_a_medication_record(fake_db):
    """At the counter, "explain this medicine" means the prescribed line."""
    result = get_medication_by_record_id(str(RX_ITEM_AMOXICILLIN))
    assert result["status"] == "success"

    record = result["record"]
    assert record["medication_name"] == "Amoxicillin"
    assert record["strength"] == "500 mg"
    assert record["medication_type"] == "Prescribed / dispensed"
    assert record["patient_id"] == PATIENT_HUMAN_ID


def test_a_prescribed_items_course_length_reaches_the_record(fake_db):
    """The duration is not lost in translation between the two shapes."""
    record = get_medication_by_record_id(str(RX_ITEM_AMOXICILLIN))["record"]
    assert "7 days" in (record["notes"] or "")


def test_a_substituted_item_says_so_on_the_record(fake_db):
    """A patient holding a different box must be told why."""
    record = get_medication_by_record_id(str(RX_ITEM_IBUPROFEN))["record"]
    assert "Brufen" in (record["notes"] or "")


# ══════════════════════════════════════════════════════════════════════════════
# 5. Prompt formatting
# ══════════════════════════════════════════════════════════════════════════════


def test_format_handles_empty_input():
    """Formatting nothing says so rather than rendering an empty sheet."""
    assert format_prescription_for_prompt({}) == "No prescription data available."
    assert format_prescription_for_prompt(None) == "No prescription data available."


def test_format_includes_everything_counselling_needs(fake_db):
    """Every fact the sheet must be built from appears in the prompt."""
    data = get_prescription_by_id(PRESCRIPTION_HUMAN_ID)
    text = format_prescription_for_prompt(data)

    assert PRESCRIPTION_HUMAN_ID in text
    assert "Amoxicillin" in text
    assert "500 mg" in text
    assert "three times a day" in text
    assert "7 days" in text
    assert "Take with food." in text
    assert "Dr. Ananya Sharma" in text


def test_format_states_the_reading_level_and_allergies(fake_db):
    """The sheet is written for this patient, so the prompt says who they are."""
    text = format_prescription_for_prompt(get_prescription_by_id(PRESCRIPTION_HUMAN_ID))
    assert "basic" in text
    assert "Penicillin" in text


def test_format_flags_a_substitution_for_the_patient(fake_db):
    """The substitution has to reach the sheet or the patient is confused."""
    text = format_prescription_for_prompt(get_prescription_by_id(PRESCRIPTION_HUMAN_ID))
    assert "Brufen" in text


def test_format_includes_the_reviewed_warnings(fake_db):
    """The pharmacist's accepted warnings are context for the counselling."""
    text = format_prescription_for_prompt(get_prescription_by_id(PRESCRIPTION_HUMAN_ID))
    assert "HIGH" in text
    assert "bleeding risk" in text.lower()
