"""Transactional appointment booking against MongoDB.

PostgreSQL gave this `SELECT … FOR UPDATE`. MongoDB has no row locks over a
computed slot grid, so double-booking is prevented with optimistic
concurrency instead:

  1. verify the slot is genuinely free *now*;
  2. insert the appointment;
  3. re-read every active appointment for that doctor/date/time.

If step 3 finds more than one, the writers raced. ObjectIds are monotonic, so
every racer independently agrees the lowest `_id` is the winner; the losers
delete their own row and report the clash. No lock, no lost booking, and no
dependency on replica-set transactions.
"""

from __future__ import annotations

import datetime
import logging
import re
from typing import Any, Dict, Optional

from pymongo.errors import DuplicateKeyError

from agents.tools.common import (
    ACTIVE_APPOINTMENT_STATUSES,
    compute_slots,
    doctor_view,
    iso_date,
    parse_slot_id,
    resolve_patient,
    resolve_doctor,
)
from database import Collections, get_db, sanitize_db_error

logger = logging.getLogger(__name__)

VALID_TYPES = {"In-person", "Telehealth", "Lab-only"}
# Maps the agent's vocabulary onto the enum the Appointment schema accepts.
TYPE_TO_SCHEMA = {
    "In-person": "specialist",
    "Telehealth": "telehealth",
    "Lab-only": "lab_only",
}
MAX_ID_ATTEMPTS = 5


def _next_appointment_id(db, when: Optional[datetime.datetime] = None) -> str:
    """Generates the next `APT-YYYYMMDD-NNNN` identifier for the day.

    Matches `idGenerator.nextId` in the Node backend so both front doors
    produce identifiers in the same series.
    """
    day = (when or datetime.datetime.now()).strftime("%Y%m%d")
    prefix = f"APT-{day}-"
    latest = (
        db[Collections.APPOINTMENTS]
        .find({"appointmentId": {"$regex": f"^{re.escape(prefix)}\\d{{4}}$"}}, {"appointmentId": 1})
        .sort("appointmentId", -1)
        .limit(1)
    )
    latest_list = list(latest)
    seq = 1
    if latest_list:
        try:
            seq = int(str(latest_list[0]["appointmentId"])[-4:]) + 1
        except (ValueError, TypeError):
            seq = 1
    return f"{prefix}{seq:04d}"


def create_appointment(
    patient_id: str,
    doctor_id: str,
    slot_id: str,
    appointment_type: str = "In-person",
    patient_confirmed: bool = False,
) -> Dict[str, Any]:
    """Books an appointment atomically, preventing double-booking.

    Validates patient identity, doctor existence and activity, slot ownership
    and real-time availability, past-date constraints, and explicit patient
    confirmation before writing anything.

    Args:
        patient_id: The verified identity of the patient booking the appointment.
        doctor_id: The target doctor's identifier.
        slot_id: The target slot ID from get_available_doctor_slots.
        appointment_type: 'In-person', 'Telehealth' or 'Lab-only'.
        patient_confirmed: Explicit boolean confirmation from the patient.

    Returns:
        Structured result detailing success with the appointment ID, or the failure reason.
    """
    if not patient_id or not str(patient_id).strip():
        return {"status": "error", "message": "Authentication required: Missing patient identity."}
    if not doctor_id or not str(doctor_id).strip():
        return {"status": "error", "message": "Doctor ID is required."}
    if not slot_id or not str(slot_id).strip():
        return {"status": "error", "message": "Slot ID is required."}
    if not patient_confirmed:
        return {
            "status": "error",
            "message": "Explicit patient confirmation is required before an appointment can be booked.",
        }

    clean_type = str(appointment_type).strip() if appointment_type else "In-person"
    if clean_type not in VALID_TYPES:
        clean_type = "In-person"

    parsed = parse_slot_id(slot_id)
    if not parsed:
        return {
            "status": "error",
            "message": (
                f"Slot identifier '{slot_id}' is not valid. Use a slot_id returned by "
                "get_available_doctor_slots."
            ),
        }

    try:
        with get_db() as db:
            # 1. Patient must exist.
            patient = resolve_patient(db, patient_id)
            if not patient:
                return {
                    "status": "error",
                    "message": (
                        f"Patient '{patient_id}' is not registered in the hospital system."
                    ),
                }

            # 2. Doctor must exist and be active.
            doctor = resolve_doctor(db, doctor_id)
            if not doctor:
                return {"status": "error", "message": f"Doctor '{doctor_id}' does not exist."}
            view = doctor_view(doctor)
            if not view["is_active"]:
                return {
                    "status": "error",
                    "message": f"{view['doctor_name']} is currently not active for bookings.",
                }

            # 3. The slot must belong to the doctor the caller named.
            if str(doctor["_id"]) != parsed["doctor_id"]:
                return {
                    "status": "error",
                    "message": (
                        f"Slot '{slot_id}' belongs to a different doctor than "
                        f"'{doctor_id}'."
                    ),
                }

            slot_date = parsed["date"]
            slot_time = parsed["time"]

            # 4. Idempotency — a retried tool call must not book twice.
            existing = db[Collections.APPOINTMENTS].find_one(
                {
                    "patientId": patient["_id"],
                    "doctorId": doctor["_id"],
                    "scheduledDate": slot_date,
                    "scheduledTime": slot_time,
                    "status": {"$in": ACTIVE_APPOINTMENT_STATUSES},
                }
            )
            if existing:
                return {
                    "status": "success",
                    "is_duplicate_retry": True,
                    "appointment_id": existing.get("appointmentId"),
                    "message": "Appointment has already been successfully booked.",
                    "booking_status": existing.get("status"),
                }

            # 5. The slot must be a real clinic slot and free right now.
            computed = compute_slots(db, doctor, slot_date, include_booked=True)
            if computed.get("reason"):
                return {"status": "error", "message": computed["reason"]}

            match = next(
                (s for s in computed["slots"] if s["start_time"] == slot_time), None
            )
            if not match:
                return {
                    "status": "error",
                    "message": (
                        f"{slot_time} is not a clinic slot for {view['doctor_name']} on "
                        f"{iso_date(slot_date)}."
                    ),
                }
            if not match["available"]:
                slot_at = slot_date + datetime.timedelta(
                    hours=int(slot_time[:2]), minutes=int(slot_time[3:])
                )
                if slot_at <= datetime.datetime.now():
                    return {"status": "error", "message": "That slot is in the past."}
                return {
                    "status": "error",
                    "message": (
                        f"Slot '{slot_id}' is no longer available (current status: "
                        f"{match['slot_status']})."
                    ),
                }

            # 6. Insert, retrying only on an identifier collision.
            slot_minutes = computed["slot_minutes"]
            fee = view["consultation_fee_inr"]
            now = datetime.datetime.now()
            inserted_id = None
            appointment_id = None

            for _ in range(MAX_ID_ATTEMPTS):
                appointment_id = _next_appointment_id(db, now)
                document = {
                    "appointmentId": appointment_id,
                    "patientId": patient["_id"],
                    "doctorId": doctor["_id"],
                    "admissionId": None,
                    "type": TYPE_TO_SCHEMA.get(clean_type, "specialist"),
                    "specialty": view["specialization"] or view["specialty_slug"],
                    "scheduledDate": slot_date,
                    "scheduledTime": slot_time,
                    "duration": slot_minutes,
                    "location": "",
                    "status": "scheduled",
                    "reason": "Booked via the appointment agent",
                    "notes": None,
                    "insuranceVerified": False,
                    "reminderSent": False,
                    "createdBy": None,
                    "source": "public",
                    "complaint": "",
                    "triage": {
                        "specialty": view["specialty_slug"],
                        "reason": "",
                        "confidence": 0,
                        "matched": [],
                        "overridden": False,
                    },
                    "createdAt": now,
                    "updatedAt": now,
                    "__v": 0,
                }
                try:
                    inserted_id = db[Collections.APPOINTMENTS].insert_one(document).inserted_id
                    break
                except DuplicateKeyError:
                    continue

            if inserted_id is None:
                return {
                    "status": "error",
                    "message": "Could not allocate an appointment number. Please try again.",
                }

            # 7. Resolve any race: lowest ObjectId wins, the rest roll back.
            contenders = list(
                db[Collections.APPOINTMENTS]
                .find(
                    {
                        "doctorId": doctor["_id"],
                        "scheduledDate": slot_date,
                        "scheduledTime": slot_time,
                        "status": {"$in": ACTIVE_APPOINTMENT_STATUSES},
                    },
                    {"_id": 1},
                )
                .sort("_id", 1)
            )
            if contenders and contenders[0]["_id"] != inserted_id:
                db[Collections.APPOINTMENTS].delete_one({"_id": inserted_id})
                return {
                    "status": "error",
                    "message": "Someone just took that slot. Please pick another time.",
                }

            end_minutes = int(slot_time[:2]) * 60 + int(slot_time[3:]) + slot_minutes
            end_time = f"{(end_minutes // 60) % 24:02d}:{end_minutes % 60:02d}"

            return {
                "status": "success",
                "appointment_id": appointment_id,
                "patient_id": patient.get("patientId"),
                "doctor_id": str(doctor["_id"]),
                "doctor_name": view["doctor_name"],
                "specialization": view["specialization"],
                "appointment_date": iso_date(slot_date),
                "start_time": slot_time,
                "end_time": end_time,
                "appointment_type": clean_type,
                "booking_status": "scheduled",
                "consultation_fee_inr": float(fee or 0),
                "message": (
                    f"Appointment confirmed with {view['doctor_name']} on "
                    f"{iso_date(slot_date)} from {slot_time} to {end_time}."
                ),
            }

    except Exception as e:
        safe = sanitize_db_error(e)
        logger.error("Appointment booking failed: %s", safe)
        return {"status": "error", "message": f"Appointment booking failed: {safe}"}


def cancel_appointment(
    appointment_id: str,
    authenticated_patient_id: str,
) -> Dict[str, Any]:
    """Cancels an existing appointment, enforcing that it belongs to the caller.

    Args:
        appointment_id: The appointment identifier (e.g. 'APT-20261031-0001').
        authenticated_patient_id: The verified identity of the requesting patient.

    Returns:
        Structured result confirming cancellation, or the reason it was refused.
    """
    if not appointment_id or not str(appointment_id).strip():
        return {"status": "error", "message": "Appointment ID is required."}
    if not authenticated_patient_id or not str(authenticated_patient_id).strip():
        return {"status": "unauthorized", "message": "Authenticated patient ID is required."}

    clean_apt = str(appointment_id).strip()

    try:
        with get_db() as db:
            appointment = db[Collections.APPOINTMENTS].find_one({"appointmentId": clean_apt})
            if not appointment:
                return {"status": "not_found", "message": f"Appointment '{clean_apt}' not found."}

            caller = resolve_patient(db, authenticated_patient_id)
            if not caller or caller.get("_id") != appointment.get("patientId"):
                return {
                    "status": "forbidden",
                    "message": "Access denied. You are not authorized to cancel this appointment.",
                }

            if appointment.get("status") not in ACTIVE_APPOINTMENT_STATUSES:
                return {
                    "status": "error",
                    "message": (
                        f"Appointment '{clean_apt}' is already "
                        f"{appointment.get('status')} and cannot be cancelled."
                    ),
                }

            db[Collections.APPOINTMENTS].update_one(
                {"_id": appointment["_id"]},
                {"$set": {"status": "cancelled", "updatedAt": datetime.datetime.now()}},
            )

            return {
                "status": "success",
                "appointment_id": clean_apt,
                "booking_status": "cancelled",
                "message": f"Appointment '{clean_apt}' has been cancelled. The slot is free again.",
            }
    except Exception as e:
        safe = sanitize_db_error(e)
        logger.error("Failed to cancel appointment '%s': %s", clean_apt, safe)
        return {"status": "error", "message": f"Failed to cancel appointment: {safe}"}
