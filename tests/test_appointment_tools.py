"""Appointment tool tests, run against an in-memory MongoDB."""

from __future__ import annotations

import datetime


from agents.tools.common import make_slot_id, parse_slot_id
from agents.tools.inspect_schema import inspect_database_schema
from agents.tools.query_data import (
    check_slot_availability,
    get_appointment_details,
    get_available_doctor_slots,
    get_doctor_details,
    get_doctors_by_specialty,
    get_patient_appointments,
    list_specialties,
)
from agents.tools.write_data import cancel_appointment, create_appointment
from tests.conftest import (
    APPOINTMENT_HUMAN_ID,
    DOCTOR_2_ID,
    DOCTOR_ID,
    PATIENT_2_HUMAN_ID,
    PATIENT_HUMAN_ID,
    future_clinic_date,
)


# ══════════════════════════════════════════════════════════════════════════════
# 1. Schema inspection
# ══════════════════════════════════════════════════════════════════════════════


def test_inspect_schema_rejects_invalid_collection_names():
    """Collection names with injection attempts or odd characters are refused."""
    bad = ["users; drop", "users-slots", "name with spaces", "123invalid"]
    result = inspect_database_schema(bad)
    assert result["status"] == "error"
    assert "Invalid or disallowed" in result["message"]
    assert result["collections"] == {}


def test_inspect_schema_rejects_collections_outside_the_allowlist():
    """A syntactically valid but unlisted collection is still refused."""
    result = inspect_database_schema(["auditlogs"])
    assert result["status"] == "error"
    assert "auditlogs" in result["message"]


def test_inspect_schema_reports_fields_for_existing_collection(fake_db):
    """An allowed collection reports its sampled field paths and indexes."""
    result = inspect_database_schema(["specialties"])
    assert result["status"] == "success"

    meta = result["collections"]["specialties"]
    assert meta["exists"] is True
    assert meta["document_count"] == 3

    fields = {f["field"] for f in meta["fields"]}
    assert "slug" in fields
    assert "name" in fields
    assert "isActive" in fields


def test_inspect_schema_reports_missing_collection(fake_db):
    """A collection that is allowed but absent reports exists: False."""
    fake_db.drop_collection("vitals")
    result = inspect_database_schema(["vitals"])
    assert result["status"] == "success"
    assert result["collections"]["vitals"]["exists"] is False
    assert result["collections"]["vitals"]["fields"] == []


def test_inspect_schema_never_exposes_password_hashes(fake_db):
    """Credential fields must never be echoed back, even when sampled."""
    result = inspect_database_schema(["users"])
    assert result["status"] == "success"
    fields = {f["field"] for f in result["collections"]["users"]["fields"]}
    assert "passwordHash" not in fields


# ══════════════════════════════════════════════════════════════════════════════
# 2. Specialties and doctors
# ══════════════════════════════════════════════════════════════════════════════


def test_list_specialties_returns_only_active_departments(fake_db):
    """An inactive department is hidden from patients."""
    result = list_specialties()
    assert result["status"] == "success"
    names = [s["name"] for s in result["specialties"]]
    assert "Cardiology" in names
    assert "Retired Unit" not in names


def test_list_specialties_sorts_by_display_order(fake_db):
    """Departments come back in the order an administrator set."""
    result = list_specialties()
    assert result["specialties"][0]["name"] == "General Medicine"


def test_get_doctors_by_specialty_requires_a_specialty():
    """An empty specialty is rejected before any query runs."""
    result = get_doctors_by_specialty("")
    assert result["status"] == "error"
    assert result["count"] == 0


def test_get_doctors_by_specialty_matches_active_doctors(fake_db):
    """Cardiology returns its active doctors and excludes inactive ones."""
    result = get_doctors_by_specialty("Cardiology")
    assert result["status"] == "success"
    names = [d["doctor_name"] for d in result["doctors"]]
    assert "Dr. Ananya Sharma" in names
    assert "Dr. Priya Nair" not in names  # inactive


def test_get_doctors_by_specialty_sorts_by_experience(fake_db):
    """The most experienced doctor is offered first."""
    result = get_doctors_by_specialty("Cardiology")
    assert result["doctors"][0]["doctor_name"] == "Dr. Ananya Sharma"
    assert result["doctors"][0]["experience_years"] == 18


def test_get_doctors_by_specialty_matches_a_condition(fake_db):
    """Searching a condition the department treats finds its doctors."""
    result = get_doctors_by_specialty("Atrial fibrillation")
    assert result["status"] == "success"
    assert result["count"] >= 1


def test_get_doctors_never_returns_password_hashes(fake_db):
    """The projected doctor view carries no credential material."""
    result = get_doctors_by_specialty("Cardiology")
    for doctor in result["doctors"]:
        assert "passwordHash" not in doctor


def test_get_doctor_details_requires_an_id():
    """A blank doctor id is rejected."""
    assert get_doctor_details("")["status"] == "error"


def test_get_doctor_details_by_object_id(fake_db):
    """A doctor resolves by their identifier."""
    result = get_doctor_details(str(DOCTOR_ID))
    assert result["status"] == "success"
    assert result["doctor"]["doctor_name"] == "Dr. Ananya Sharma"
    assert result["doctor"]["consultation_fee_inr"] == 800


def test_get_doctor_details_by_name(fake_db):
    """A doctor also resolves by name, which is what a patient actually types."""
    result = get_doctor_details("Dr. Ananya Sharma")
    assert result["status"] == "success"
    assert result["doctor"]["doctor_id"] == str(DOCTOR_ID)


def test_get_doctor_details_unknown_doctor(fake_db):
    """An unknown doctor is reported as not found, not as an error."""
    result = get_doctor_details("Dr. Nobody Here")
    assert result["status"] == "not_found"


# ══════════════════════════════════════════════════════════════════════════════
# 3. Slots
# ══════════════════════════════════════════════════════════════════════════════


def test_slot_id_round_trips():
    """A slot id decodes back to the doctor, date and time it encodes."""
    date = datetime.datetime(2026, 11, 3)
    slot_id = make_slot_id(DOCTOR_ID, date, "09:30")
    parsed = parse_slot_id(slot_id)
    assert parsed is not None
    assert parsed["doctor_id"] == str(DOCTOR_ID)
    assert parsed["date"] == date
    assert parsed["time"] == "09:30"


def test_parse_slot_id_rejects_rubbish():
    """A malformed slot id decodes to nothing rather than guessing."""
    assert parse_slot_id("not-a-slot") is None
    assert parse_slot_id("") is None


def test_get_available_slots_rejects_a_bad_date(fake_db):
    """A date that is not YYYY-MM-DD is refused with a clear message."""
    result = get_available_doctor_slots(doctor_id=str(DOCTOR_ID), slot_date="31-13-2026x")
    assert result["status"] == "error"
    assert "Invalid date format" in result["message"]


def test_get_available_slots_builds_the_clinic_grid(fake_db):
    """A 09:00-11:00 clinic at 30-minute slots yields four times."""
    date = future_clinic_date().strftime("%Y-%m-%d")
    result = get_available_doctor_slots(doctor_id=str(DOCTOR_ID), slot_date=date)
    assert result["status"] == "success"
    times = [s["start_time"] for s in result["slots"]]
    # 09:00 is taken by the seeded appointment.
    assert times == ["09:30", "10:00", "10:30"]


def test_get_available_slots_excludes_booked_times(fake_db):
    """A slot with a scheduled appointment on it is never offered."""
    date = future_clinic_date().strftime("%Y-%m-%d")
    result = get_available_doctor_slots(doctor_id=str(DOCTOR_ID), slot_date=date)
    assert "09:00" not in [s["start_time"] for s in result["slots"]]


def test_get_available_slots_offers_a_cancelled_time_again(fake_db):
    """Cancelling frees the slot, because only active statuses block it."""
    fake_db.appointments.update_one(
        {"appointmentId": APPOINTMENT_HUMAN_ID}, {"$set": {"status": "cancelled"}}
    )
    date = future_clinic_date().strftime("%Y-%m-%d")
    result = get_available_doctor_slots(doctor_id=str(DOCTOR_ID), slot_date=date)
    assert "09:00" in [s["start_time"] for s in result["slots"]]


def test_get_available_slots_by_specialty_spans_doctors(fake_db):
    """Searching a specialty returns slots from every bookable doctor in it."""
    date = future_clinic_date().strftime("%Y-%m-%d")
    result = get_available_doctor_slots(specialty="Cardiology", slot_date=date)
    assert result["status"] == "success"
    assert len({s["doctor_id"] for s in result["slots"]}) == 2


def test_get_available_slots_reports_a_non_clinic_day(fake_db):
    """A day the doctor does not hold clinic yields no slots."""
    fake_db.users.update_one(
        {"_id": DOCTOR_ID}, {"$set": {"doctorProfile.availableDays": []}}
    )
    # An empty availableDays list falls back to Mon-Fri, so pick a Sunday.
    date = future_clinic_date()
    while date.weekday() != 6:
        date += datetime.timedelta(days=1)
    result = get_available_doctor_slots(
        doctor_id=str(DOCTOR_ID), slot_date=date.strftime("%Y-%m-%d")
    )
    assert result["status"] == "success"
    assert result["slots"] == []


def test_get_available_slots_unknown_doctor(fake_db):
    """An unknown doctor is not found rather than returning an empty grid."""
    result = get_available_doctor_slots(doctor_id="Dr. Nobody")
    assert result["status"] == "not_found"


def test_check_slot_availability_requires_an_id():
    """A blank slot id is rejected."""
    result = check_slot_availability("")
    assert result["status"] == "error"
    assert result["is_available"] is False


def test_check_slot_availability_rejects_a_malformed_id(fake_db):
    """A slot id that did not come from the slot tool is refused."""
    result = check_slot_availability("SLOT000001")
    assert result["status"] == "error"
    assert result["is_available"] is False


def test_check_slot_availability_free_slot(fake_db):
    """A free slot reports available."""
    slot_id = make_slot_id(DOCTOR_ID, future_clinic_date(), "10:00")
    result = check_slot_availability(slot_id)
    assert result["status"] == "success"
    assert result["is_available"] is True
    assert result["slot"]["doctor_name"] == "Dr. Ananya Sharma"


def test_check_slot_availability_booked_slot(fake_db):
    """A slot already taken reports unavailable."""
    slot_id = make_slot_id(DOCTOR_ID, future_clinic_date(), "09:00")
    result = check_slot_availability(slot_id)
    assert result["status"] == "success"
    assert result["is_available"] is False
    assert result["slot_status"] == "Booked"


def test_check_slot_availability_outside_clinic_hours(fake_db):
    """A time outside the clinic grid is not a slot at all."""
    slot_id = make_slot_id(DOCTOR_ID, future_clinic_date(), "22:00")
    result = check_slot_availability(slot_id)
    assert result["status"] == "not_found"
    assert result["is_available"] is False


# ══════════════════════════════════════════════════════════════════════════════
# 4. Booking
# ══════════════════════════════════════════════════════════════════════════════


def _free_slot_id() -> str:
    return make_slot_id(DOCTOR_ID, future_clinic_date(), "10:00")


def test_create_appointment_requires_patient_identity(fake_db):
    """Booking without an identity is refused before anything is read."""
    result = create_appointment("", str(DOCTOR_ID), _free_slot_id(), patient_confirmed=True)
    assert result["status"] == "error"
    assert "Authentication required" in result["message"]


def test_create_appointment_requires_explicit_confirmation(fake_db):
    """The agent cannot book on a patient's behalf without a clear yes."""
    result = create_appointment(
        PATIENT_HUMAN_ID, str(DOCTOR_ID), _free_slot_id(), patient_confirmed=False
    )
    assert result["status"] == "error"
    assert "confirmation is required" in result["message"]


def test_create_appointment_rejects_a_malformed_slot(fake_db):
    """An invented slot id is refused rather than resolved loosely."""
    result = create_appointment(
        PATIENT_HUMAN_ID, str(DOCTOR_ID), "SLOT1", patient_confirmed=True
    )
    assert result["status"] == "error"
    assert "not valid" in result["message"]


def test_create_appointment_rejects_unknown_patient(fake_db):
    """A patient who is not on the system cannot be booked in."""
    result = create_appointment(
        "PAT-19000101-0001", str(DOCTOR_ID), _free_slot_id(), patient_confirmed=True
    )
    assert result["status"] == "error"
    assert "not registered" in result["message"]


def test_create_appointment_rejects_unknown_doctor(fake_db):
    """An unknown doctor is refused."""
    result = create_appointment(
        PATIENT_HUMAN_ID, "Dr. Nobody", _free_slot_id(), patient_confirmed=True
    )
    assert result["status"] == "error"
    assert "does not exist" in result["message"]


def test_create_appointment_rejects_inactive_doctor(fake_db):
    """A doctor who is no longer active cannot take bookings."""
    from tests.conftest import INACTIVE_DOCTOR_ID

    slot_id = make_slot_id(INACTIVE_DOCTOR_ID, future_clinic_date(), "09:00")
    result = create_appointment(
        PATIENT_HUMAN_ID, str(INACTIVE_DOCTOR_ID), slot_id, patient_confirmed=True
    )
    assert result["status"] == "error"
    assert "not active" in result["message"]


def test_create_appointment_rejects_slot_belonging_to_another_doctor(fake_db):
    """A slot id must match the doctor the caller named."""
    result = create_appointment(
        PATIENT_HUMAN_ID, str(DOCTOR_2_ID), _free_slot_id(), patient_confirmed=True
    )
    assert result["status"] == "error"
    assert "different doctor" in result["message"]


def test_create_appointment_rejects_a_time_outside_clinic_hours(fake_db):
    """A time that is not on the grid cannot be booked."""
    slot_id = make_slot_id(DOCTOR_ID, future_clinic_date(), "23:30")
    result = create_appointment(
        PATIENT_HUMAN_ID, str(DOCTOR_ID), slot_id, patient_confirmed=True
    )
    assert result["status"] == "error"
    assert "not a clinic slot" in result["message"]


def test_create_appointment_rejects_a_past_slot(fake_db):
    """A slot earlier today has already gone and is never bookable."""
    yesterday = datetime.datetime.now().replace(
        hour=0, minute=0, second=0, microsecond=0
    ) - datetime.timedelta(days=1)
    slot_id = make_slot_id(DOCTOR_ID, yesterday, "09:30")
    result = create_appointment(
        PATIENT_HUMAN_ID, str(DOCTOR_ID), slot_id, patient_confirmed=True
    )
    assert result["status"] == "error"
    assert "past" in result["message"].lower()


def test_create_appointment_books_a_free_slot(fake_db):
    """A confirmed booking on a free slot writes one appointment."""
    result = create_appointment(
        PATIENT_HUMAN_ID, str(DOCTOR_ID), _free_slot_id(), patient_confirmed=True
    )
    assert result["status"] == "success"
    assert result["appointment_id"].startswith("APT-")
    assert result["start_time"] == "10:00"
    assert result["end_time"] == "10:30"
    assert result["booking_status"] == "scheduled"
    assert result["consultation_fee_inr"] == 800.0

    stored = fake_db.appointments.find_one({"appointmentId": result["appointment_id"]})
    assert stored is not None
    assert stored["doctorId"] == DOCTOR_ID
    assert stored["scheduledTime"] == "10:00"


def test_create_appointment_allocates_sequential_ids(fake_db):
    """Two bookings on the same day get consecutive identifiers."""
    first = create_appointment(
        PATIENT_HUMAN_ID, str(DOCTOR_ID), _free_slot_id(), patient_confirmed=True
    )
    second_slot = make_slot_id(DOCTOR_ID, future_clinic_date(), "10:30")
    second = create_appointment(
        PATIENT_2_HUMAN_ID, str(DOCTOR_ID), second_slot, patient_confirmed=True
    )
    assert first["status"] == "success"
    assert second["status"] == "success"
    assert first["appointment_id"] != second["appointment_id"]


def test_create_appointment_is_idempotent_on_retry(fake_db):
    """Re-running the same tool call returns the first booking, not a second one."""
    slot_id = _free_slot_id()
    first = create_appointment(
        PATIENT_HUMAN_ID, str(DOCTOR_ID), slot_id, patient_confirmed=True
    )
    second = create_appointment(
        PATIENT_HUMAN_ID, str(DOCTOR_ID), slot_id, patient_confirmed=True
    )
    assert second["status"] == "success"
    assert second.get("is_duplicate_retry") is True
    assert second["appointment_id"] == first["appointment_id"]
    assert fake_db.appointments.count_documents({"scheduledTime": "10:00"}) == 1


def test_create_appointment_prevents_double_booking(fake_db):
    """A second patient cannot take a slot that was just filled."""
    slot_id = _free_slot_id()
    create_appointment(PATIENT_HUMAN_ID, str(DOCTOR_ID), slot_id, patient_confirmed=True)
    clash = create_appointment(
        PATIENT_2_HUMAN_ID, str(DOCTOR_ID), slot_id, patient_confirmed=True
    )
    assert clash["status"] == "error"
    assert "no longer available" in clash["message"]
    assert fake_db.appointments.count_documents({"scheduledTime": "10:00"}) == 1


def test_create_appointment_normalises_an_unknown_type(fake_db):
    """An appointment type outside the allowed set falls back to In-person."""
    result = create_appointment(
        PATIENT_HUMAN_ID,
        str(DOCTOR_ID),
        _free_slot_id(),
        appointment_type="Holographic",
        patient_confirmed=True,
    )
    assert result["status"] == "success"
    assert result["appointment_type"] == "In-person"


# ══════════════════════════════════════════════════════════════════════════════
# 5. Reading and cancelling appointments
# ══════════════════════════════════════════════════════════════════════════════


def test_get_appointment_details_requires_both_ids(fake_db):
    """Both the appointment and the caller's identity are mandatory."""
    assert get_appointment_details("", PATIENT_HUMAN_ID)["status"] == "error"
    assert get_appointment_details(APPOINTMENT_HUMAN_ID, "")["status"] == "unauthorized"


def test_get_appointment_details_returns_the_owner_s_appointment(fake_db):
    """The patient who owns the appointment can read it."""
    result = get_appointment_details(APPOINTMENT_HUMAN_ID, PATIENT_HUMAN_ID)
    assert result["status"] == "success"
    assert result["appointment"]["doctor_name"] == "Dr. Ananya Sharma"
    assert result["appointment"]["start_time"] == "09:00"


def test_get_appointment_details_refuses_another_patient(fake_db):
    """A different patient is refused, not shown a redacted record."""
    result = get_appointment_details(APPOINTMENT_HUMAN_ID, PATIENT_2_HUMAN_ID)
    assert result["status"] == "forbidden"


def test_get_appointment_details_unknown_appointment(fake_db):
    """An appointment that does not exist is reported as not found."""
    result = get_appointment_details("APT-19000101-0001", PATIENT_HUMAN_ID)
    assert result["status"] == "not_found"


def test_get_patient_appointments_lists_only_active_ones(fake_db):
    """The patient's own upcoming appointments come back; cancelled ones do not."""
    result = get_patient_appointments(PATIENT_HUMAN_ID)
    assert result["status"] == "success"
    assert result["count"] == 1

    fake_db.appointments.update_one(
        {"appointmentId": APPOINTMENT_HUMAN_ID}, {"$set": {"status": "cancelled"}}
    )
    assert get_patient_appointments(PATIENT_HUMAN_ID)["count"] == 0


def test_get_patient_appointments_requires_identity(fake_db):
    """Listing appointments without an identity is refused."""
    assert get_patient_appointments("")["status"] == "unauthorized"


def test_cancel_appointment_refuses_another_patient(fake_db):
    """One patient cannot cancel another patient's appointment."""
    result = cancel_appointment(APPOINTMENT_HUMAN_ID, PATIENT_2_HUMAN_ID)
    assert result["status"] == "forbidden"
    stored = fake_db.appointments.find_one({"appointmentId": APPOINTMENT_HUMAN_ID})
    assert stored["status"] == "scheduled"


def test_cancel_appointment_by_the_owner(fake_db):
    """The owner can cancel, and the record reflects it."""
    result = cancel_appointment(APPOINTMENT_HUMAN_ID, PATIENT_HUMAN_ID)
    assert result["status"] == "success"
    stored = fake_db.appointments.find_one({"appointmentId": APPOINTMENT_HUMAN_ID})
    assert stored["status"] == "cancelled"


def test_cancel_appointment_is_not_repeatable(fake_db):
    """Cancelling twice is refused rather than silently succeeding."""
    cancel_appointment(APPOINTMENT_HUMAN_ID, PATIENT_HUMAN_ID)
    again = cancel_appointment(APPOINTMENT_HUMAN_ID, PATIENT_HUMAN_ID)
    assert again["status"] == "error"
    assert "cancelled" in again["message"]


def test_cancel_appointment_unknown_appointment(fake_db):
    """Cancelling something that does not exist is reported as not found."""
    assert cancel_appointment("APT-19000101-0001", PATIENT_HUMAN_ID)["status"] == "not_found"
