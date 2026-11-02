"""Discharge summary tool tests, run against an in-memory MongoDB."""

from __future__ import annotations

from agents.tools.discharge_summary_tools import (
    format_discharge_package_for_prompt,
    get_discharge_package_by_id,
    list_discharge_packages,
)
from tests.conftest import (
    ADMISSION_HUMAN_ID,
    PACKAGE_HUMAN_ID,
    PATIENT_HUMAN_ID,
)


# ══════════════════════════════════════════════════════════════════════════════
# 1. Validation and lookup
# ══════════════════════════════════════════════════════════════════════════════


def test_get_package_requires_an_id():
    """A blank package id is refused with the standard empty envelope."""
    result = get_discharge_package_by_id("")
    assert result["status"] == "error"
    assert result["found"] is False
    assert result["package"] is None
    assert result["medications"] == []
    assert "Missing package_id parameter." in result["missing_information"]


def test_get_package_handles_none():
    """A None identifier is a validation error, not a crash."""
    result = get_discharge_package_by_id(None)
    assert result["status"] == "error"
    assert result["found"] is False


def test_get_package_unknown_id(fake_db):
    """An unknown package reports not_found and says what is missing."""
    result = get_discharge_package_by_id("DIS-19000101-0001")
    assert result["status"] == "not_found"
    assert result["found"] is False
    assert result["package"] is None
    assert any("does not exist" in m for m in result["missing_information"])


def test_get_package_is_case_insensitive(fake_db):
    """A lower-cased package id still resolves."""
    result = get_discharge_package_by_id(PACKAGE_HUMAN_ID.lower())
    assert result["status"] == "success"
    assert result["package"]["package_id"] == PACKAGE_HUMAN_ID


def test_get_package_returns_the_package(fake_db):
    """The package's own fields are flattened into the agent's shape."""
    result = get_discharge_package_by_id(PACKAGE_HUMAN_ID)
    assert result["status"] == "success"
    assert result["found"] is True

    package = result["package"]
    assert package["package_id"] == PACKAGE_HUMAN_ID
    assert package["admission_id"] == ADMISSION_HUMAN_ID
    assert package["package_status"] == "pending_review"
    assert package["medication_flags_count"] == 1
    assert package["safety_validation_status"] == "Passed"


def test_get_package_summarises_review_status(fake_db):
    """Each role's latest recorded action becomes its review status."""
    package = get_discharge_package_by_id(PACKAGE_HUMAN_ID)["package"]
    assert package["physician_review_status"] == "Approved"
    assert package["pharmacist_review_status"] == "Pending"
    assert package["nurse_review_status"] == "Pending"


def test_get_package_averages_agent_confidence(fake_db):
    """The headline confidence is the mean of the sections that produced one."""
    package = get_discharge_package_by_id(PACKAGE_HUMAN_ID)["package"]
    assert package["confidence_score"] == 0.84


def test_get_package_renders_the_follow_up_plan(fake_db):
    """Structured recommendations are flattened into readable text."""
    package = get_discharge_package_by_id(PACKAGE_HUMAN_ID)["package"]
    assert "Heart failure clinic review" in package["follow_up_plan"]
    assert "within 7 days" in package["follow_up_plan"]


# ══════════════════════════════════════════════════════════════════════════════
# 2. Related clinical records
# ══════════════════════════════════════════════════════════════════════════════


def test_get_package_includes_the_patient(fake_db):
    """Patient demographics and history travel with the package."""
    patient = get_discharge_package_by_id(PACKAGE_HUMAN_ID)["patient"]
    assert patient["patient_id"] == PATIENT_HUMAN_ID
    assert patient["first_name"] == "Senthil"
    assert patient["blood_group"] == "AB-"
    assert "Penicillin" in patient["allergies"]
    assert patient["has_known_allergy"] is True


def test_get_package_includes_the_admission(fake_db):
    """Admission details, diagnosis codes and the bed are resolved."""
    admission = get_discharge_package_by_id(PACKAGE_HUMAN_ID)["admission"]
    assert admission["admission_id"] == ADMISSION_HUMAN_ID
    assert admission["admitting_diagnosis"] == "Acute decompensated heart failure"
    assert "I50.23" in admission["icd10_code"]
    assert admission["bed_number"] == "Cardiology 3B 301 A"
    assert admission["fall_risk"] == "moderate"


def test_get_package_includes_medications(fake_db):
    """Every medication on the episode is attached."""
    meds = get_discharge_package_by_id(PACKAGE_HUMAN_ID)["medications"]
    assert len(meds) == 3
    assert {m["medication_name"] for m in meds} == {"Lisinopril", "Warfarin", "Aspirin"}


def test_get_package_includes_embedded_procedures(fake_db):
    """Procedures embedded in the admission are surfaced as their own list."""
    procedures = get_discharge_package_by_id(PACKAGE_HUMAN_ID)["procedures"]
    assert len(procedures) == 1
    assert procedures[0]["procedure_name"] == "Transthoracic echocardiogram"


def test_get_package_includes_embedded_lab_results(fake_db):
    """Lab results are surfaced, with abnormal ones flagged."""
    labs = get_discharge_package_by_id(PACKAGE_HUMAN_ID)["lab_results"]
    assert len(labs) == 2
    abnormal = [l for l in labs if l["abnormal_flag"]]
    assert len(abnormal) == 1
    assert abnormal[0]["test_name"] == "NT-proBNP"


def test_get_package_includes_embedded_clinical_notes(fake_db):
    """Clinical notes are surfaced with their category."""
    notes = get_discharge_package_by_id(PACKAGE_HUMAN_ID)["clinical_notes"]
    assert len(notes) == 1
    assert notes[0]["note_category"] == "assessment"
    assert "atrial fibrillation" in notes[0]["note_text"]


def test_get_package_includes_vitals(fake_db):
    """Vitals come from their own collection and are mapped to clinical names."""
    vitals = get_discharge_package_by_id(PACKAGE_HUMAN_ID)["vitals"]
    assert len(vitals) == 1
    assert vitals[0]["systolic_bp"] == 139
    assert vitals[0]["heart_rate_bpm"] == 109
    assert vitals[0]["spo2_percent"] == 93


# ══════════════════════════════════════════════════════════════════════════════
# 3. Missing information
# ══════════════════════════════════════════════════════════════════════════════


def test_get_package_reports_pending_completion(fake_db):
    """An unfinished package says so rather than implying it is done."""
    missing = get_discharge_package_by_id(PACKAGE_HUMAN_ID)["missing_information"]
    assert any("completion timestamp" in m for m in missing)
    assert any("discharge date is unrecorded" in m for m in missing)


def test_get_package_reports_absent_record_types(fake_db):
    """Record types with nothing recorded are named explicitly."""
    fake_db.vitals.delete_many({})
    fake_db.medications.delete_many({})
    missing = get_discharge_package_by_id(PACKAGE_HUMAN_ID)["missing_information"]
    assert any("No vitals" in m for m in missing)
    assert any("medication records" in m for m in missing)


def test_get_package_reports_a_missing_admission(fake_db):
    """A package whose admission has gone is reported, not silently empty."""
    fake_db.admissions.delete_many({})
    result = get_discharge_package_by_id(PACKAGE_HUMAN_ID)
    assert result["status"] == "success"
    assert result["admission"] is None
    assert any("admission record" in m for m in result["missing_information"])


def test_get_package_reports_a_database_failure(fake_db, monkeypatch):
    """A driver failure returns a sanitized error envelope."""
    import database as database_module

    def explode(*_args, **_kwargs):
        raise RuntimeError("connection reset")

    monkeypatch.setattr(database_module, "get_database", explode)
    result = get_discharge_package_by_id(PACKAGE_HUMAN_ID)
    assert result["status"] == "error"
    assert result["found"] is False
    assert "connection reset" in result["message"]


# ══════════════════════════════════════════════════════════════════════════════
# 4. Listing packages
# ══════════════════════════════════════════════════════════════════════════════


def test_list_packages(fake_db):
    """Packages are listed with the identifiers a report needs."""
    result = list_discharge_packages()
    assert result["status"] == "success"
    assert result["count"] == 1
    entry = result["packages"][0]
    assert entry["package_id"] == PACKAGE_HUMAN_ID
    assert entry["patient_name"] == "Senthil Kumar"
    assert entry["admitting_diagnosis"] == "Acute decompensated heart failure"


def test_list_packages_filters_by_status(fake_db):
    """A status filter narrows the list."""
    assert list_discharge_packages(status="pending_review")["count"] == 1
    assert list_discharge_packages(status="completed")["count"] == 0


# ══════════════════════════════════════════════════════════════════════════════
# 5. Prompt formatting
# ══════════════════════════════════════════════════════════════════════════════


def test_format_package_handles_empty_input():
    """Formatting nothing says so."""
    assert format_discharge_package_for_prompt({}) == "No discharge package data available."
    assert format_discharge_package_for_prompt(None) == "No discharge package data available."


def test_format_package_renders_all_sections(fake_db):
    """Every populated section appears in the formatted prompt."""
    data = get_discharge_package_by_id(PACKAGE_HUMAN_ID)
    text = format_discharge_package_for_prompt(data)

    assert "### 1. Discharge Package Overview" in text
    assert "### 2. Patient Demographics & Baseline" in text
    assert "### 3. Admission & Diagnosis Details" in text
    assert "### 4. Recorded Clinical Summary & Instructions" in text
    assert "### 5. Documented Medications" in text
    assert "### 6. Documented Procedures" in text
    assert "### 7. Laboratory & Investigation Results" in text
    assert "### 8. Clinical Notes" in text
    assert "### 9. Vitals Trend (most recent 5)" in text
    assert "### 10. Identified Missing or Pending Information" in text


def test_format_package_includes_the_clinical_detail(fake_db):
    """The formatted text carries the facts the agent must not invent."""
    data = get_discharge_package_by_id(PACKAGE_HUMAN_ID)
    text = format_discharge_package_for_prompt(data)

    assert PACKAGE_HUMAN_ID in text
    assert "Senthil" in text
    assert "Acute decompensated heart failure" in text
    assert "Warfarin" in text
    assert "NT-proBNP" in text
    assert "Transthoracic echocardiogram" in text
    assert "Penicillin" in text


def test_format_package_states_when_a_section_is_empty(fake_db):
    """An empty section says nothing was documented rather than being omitted."""
    fake_db.admissions.update_one({}, {"$set": {"procedures": [], "labResults": []}})
    data = get_discharge_package_by_id(PACKAGE_HUMAN_ID)
    text = format_discharge_package_for_prompt(data)
    assert "No procedural interventions documented." in text
    assert "No laboratory investigations documented." in text
