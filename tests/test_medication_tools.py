"""Medication tool tests, run against an in-memory MongoDB."""

from __future__ import annotations

from agents.tools.medication_tools import (
    format_medication_record_for_prompt,
    get_medication_by_record_id,
    list_medications_for_admission,
    list_medications_for_patient,
)
from tests.conftest import (
    ADMISSION_HUMAN_ID,
    MED_DISCHARGE_DOC_ID,
    MED_ITEM_ASPIRIN,
    MED_ITEM_LISINOPRIL,
    MED_ITEM_WARFARIN,
    PATIENT_2_HUMAN_ID,
    PATIENT_HUMAN_ID,
)


# ══════════════════════════════════════════════════════════════════════════════
# 1. Single record lookup
# ══════════════════════════════════════════════════════════════════════════════


def test_get_medication_requires_a_record_id():
    """A blank identifier is refused before any query runs."""
    result = get_medication_by_record_id("")
    assert result["status"] == "error"
    assert result["found"] is False
    assert result["record"] is None
    assert "required" in result["message"]


def test_get_medication_handles_none():
    """A None identifier is handled as a validation error, not a crash."""
    result = get_medication_by_record_id(None)
    assert result["status"] == "error"
    assert result["found"] is False


def test_get_medication_by_item_id(fake_db):
    """An embedded medication item resolves by its own ObjectId."""
    result = get_medication_by_record_id(str(MED_ITEM_LISINOPRIL))
    assert result["status"] == "success"
    assert result["found"] is True

    record = result["record"]
    assert record["medication_name"] == "Lisinopril"
    assert record["drug_class"] == "ACE inhibitor"
    assert record["route"] == "oral"
    assert record["medication_status"] == "Active"


def test_get_medication_resolves_the_owning_patient_and_admission(fake_db):
    """The record carries the human identifiers of its patient and admission."""
    result = get_medication_by_record_id(str(MED_ITEM_LISINOPRIL))
    record = result["record"]
    assert record["patient_id"] == PATIENT_HUMAN_ID
    assert record["admission_id"] == ADMISSION_HUMAN_ID


def test_get_medication_parses_strength_into_value_and_unit(fake_db):
    """'10 mg' is split into a numeric value and a unit for structured use."""
    record = get_medication_by_record_id(str(MED_ITEM_LISINOPRIL))["record"]
    assert record["strength"] == "10 mg"
    assert record["strength_value"] == 10.0
    assert record["strength_unit"] == "mg"


def test_get_medication_builds_dose_instructions(fake_db):
    """Dosage, frequency and any clinical note combine into one instruction line."""
    record = get_medication_by_record_id(str(MED_ITEM_WARFARIN))["record"]
    assert "5 mg" in record["dose_instructions"]
    assert "once daily in the evening" in record["dose_instructions"]
    assert "INR" in record["dose_instructions"]


def test_get_medication_labels_the_list_type(fake_db):
    """The stored listType is presented in the language a clinician reads."""
    pre = get_medication_by_record_id(str(MED_ITEM_LISINOPRIL))["record"]
    discharge = get_medication_by_record_id(str(MED_ITEM_ASPIRIN))["record"]
    assert pre["medication_type"] == "Pre-admission"
    assert discharge["medication_type"] == "Discharge"


def test_get_medication_by_parent_and_index(fake_db):
    """The '<listDocId>:<index>' form resolves the same record."""
    result = get_medication_by_record_id(f"{MED_DISCHARGE_DOC_ID}:0")
    assert result["status"] == "success"
    assert result["record"]["medication_name"] == "Aspirin"


def test_get_medication_rejects_an_out_of_range_index(fake_db):
    """An index past the end of the list is not found, not a crash."""
    result = get_medication_by_record_id(f"{MED_DISCHARGE_DOC_ID}:99")
    assert result["status"] == "not_found"
    assert result["record"] is None


def test_get_medication_unknown_record(fake_db):
    """An identifier that matches nothing reports not_found."""
    result = get_medication_by_record_id("ffffffffffffffffffffffff")
    assert result["status"] == "not_found"
    assert result["found"] is False
    assert "ffffffffffffffffffffffff" in result["message"]


def test_get_medication_rejects_a_nonsense_identifier(fake_db):
    """A string that is not an identifier at all reports not_found cleanly."""
    result = get_medication_by_record_id("MED000001")
    assert result["status"] == "not_found"
    assert result["record"] is None


def test_get_medication_reports_a_database_failure(fake_db, monkeypatch):
    """A driver failure returns a sanitized error, never a stack trace."""
    import database as database_module

    def explode(*_args, **_kwargs):
        raise RuntimeError("connection reset")

    monkeypatch.setattr(database_module, "get_database", explode)
    result = get_medication_by_record_id(str(MED_ITEM_LISINOPRIL))
    assert result["status"] == "error"
    assert result["found"] is False
    assert "connection reset" in result["message"]


# ══════════════════════════════════════════════════════════════════════════════
# 2. Listing a patient's medications
# ══════════════════════════════════════════════════════════════════════════════


def test_list_medications_requires_a_patient_id():
    """A blank patient id is refused."""
    result = list_medications_for_patient("")
    assert result["status"] == "error"
    assert result["count"] == 0


def test_list_medications_for_patient(fake_db):
    """Every medication across every list for the patient comes back."""
    result = list_medications_for_patient(PATIENT_HUMAN_ID)
    assert result["status"] == "success"
    assert result["count"] == 3
    names = {r["medication_name"] for r in result["records"]}
    assert names == {"Lisinopril", "Warfarin", "Aspirin"}


def test_list_medications_filters_by_list_type(fake_db):
    """Asking for the discharge list returns only discharge medications."""
    result = list_medications_for_patient(PATIENT_HUMAN_ID, list_type="discharge")
    assert result["count"] == 1
    assert result["records"][0]["medication_name"] == "Aspirin"


def test_list_medications_every_record_carries_an_id(fake_db):
    """Each listed record exposes the id the explain tool needs."""
    result = list_medications_for_patient(PATIENT_HUMAN_ID)
    for record in result["records"]:
        assert record["medication_record_id"]
        assert get_medication_by_record_id(record["medication_record_id"])["found"] is True


def test_list_medications_for_patient_with_none(fake_db):
    """A patient with no medications returns an empty list, not an error."""
    result = list_medications_for_patient(PATIENT_2_HUMAN_ID)
    assert result["status"] == "success"
    assert result["count"] == 0


def test_list_medications_unknown_patient(fake_db):
    """An unknown patient is reported as not found."""
    result = list_medications_for_patient("PAT-19000101-0001")
    assert result["status"] == "not_found"
    assert result["count"] == 0


def test_list_medications_for_admission(fake_db):
    """Listing by admission returns the whole episode's medications."""
    result = list_medications_for_admission(ADMISSION_HUMAN_ID)
    assert result["status"] == "success"
    assert result["count"] == 3
    assert result["admission_id"] == ADMISSION_HUMAN_ID


def test_list_medications_for_admission_filters_by_list_type(fake_db):
    """The list type filter applies to the admission view too."""
    result = list_medications_for_admission(ADMISSION_HUMAN_ID, list_type="pre_admission")
    assert result["count"] == 2


def test_list_medications_unknown_admission(fake_db):
    """An unknown admission is reported as not found."""
    result = list_medications_for_admission("ADM-19000101-0001")
    assert result["status"] == "not_found"


def test_list_medications_for_admission_requires_an_id():
    """A blank admission id is refused."""
    assert list_medications_for_admission("")["status"] == "error"


# ══════════════════════════════════════════════════════════════════════════════
# 3. Prompt formatting
# ══════════════════════════════════════════════════════════════════════════════


def test_format_medication_handles_an_empty_record():
    """Formatting nothing says so rather than rendering an empty template."""
    assert format_medication_record_for_prompt({}) == "No medication record provided."
    assert format_medication_record_for_prompt(None) == "No medication record provided."


def test_format_medication_renders_every_field(fake_db):
    """The formatted block carries the details the agent must explain."""
    record = get_medication_by_record_id(str(MED_ITEM_WARFARIN))["record"]
    text = format_medication_record_for_prompt(record)
    assert "Warfarin" in text
    assert "Anticoagulant" in text
    assert "5 mg" in text
    assert "oral" in text
    assert "Pre-admission" in text
    assert "INR" in text


def test_format_medication_omits_absent_notes(fake_db):
    """A record with no clinical note does not render an empty note line."""
    record = get_medication_by_record_id(str(MED_ITEM_ASPIRIN))["record"]
    text = format_medication_record_for_prompt(record)
    assert "Clinical Notes" not in text
