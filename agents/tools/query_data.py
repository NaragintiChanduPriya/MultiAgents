"""Read-only appointment tools, backed by MongoDB.

Doctors live in `users` (role: physician, with a doctorProfile); slots are
computed from the doctor's clinic configuration rather than stored, so these
tools stay in step with whatever the Node backend's booking flow would offer.
"""

from __future__ import annotations

import datetime
import logging
import re
from typing import Any, Dict, List, Optional

from agents.tools.common import (
    ACTIVE_APPOINTMENT_STATUSES,
    compute_slots,
    doctor_view,
    iso_date,
    parse_date,
    parse_slot_id,
    resolve_doctor,
    resolve_patient,
    upcoming_clinic_days,
)
from database import Collections, get_db, sanitize_db_error, serialize

logger = logging.getLogger(__name__)

MAX_SLOTS = 20


def get_doctors_by_specialty(specialty: str) -> Dict[str, Any]:
    """Retrieves active doctors matching a given medical specialty or department.

    Args:
        specialty: The specialty or department name (e.g. 'Cardiology', 'Orthopaedics').

    Returns:
        Structured result containing matching active doctors.
    """
    if not specialty or not str(specialty).strip():
        return {
            "status": "error",
            "message": "Specialty parameter is required.",
            "count": 0,
            "doctors": [],
        }

    clean = str(specialty).strip()
    pattern = {"$regex": re.escape(clean), "$options": "i"}

    try:
        with get_db() as db:
            # A patient may type the display name ("Orthopaedics"), the slug
            # ("orthopaedics") or a condition the department treats, so widen
            # the search through the specialties collection first.
            slugs: List[str] = []
            for spec in db[Collections.SPECIALTIES].find(
                {
                    "isActive": True,
                    "$or": [
                        {"name": pattern},
                        {"slug": pattern},
                        {"commonConditions": pattern},
                    ],
                },
                {"slug": 1},
            ):
                if spec.get("slug"):
                    slugs.append(spec["slug"])

            conditions: List[Dict[str, Any]] = [
                {"specialization": pattern},
                {"department": pattern},
                {"doctorProfile.specialty": pattern},
            ]
            if slugs:
                conditions.append({"doctorProfile.specialty": {"$in": slugs}})

            cursor = db[Collections.USERS].find(
                {"role": "physician", "isActive": True, "$or": conditions},
                {"passwordHash": 0},
            )
            doctors = [doctor_view(d) for d in cursor]
            doctors.sort(
                key=lambda d: (-(d.get("experience_years") or 0), d.get("doctor_name") or "")
            )

            return {
                "status": "success",
                "count": len(doctors),
                "specialty_searched": clean,
                "doctors": doctors,
            }
    except Exception as e:
        safe = sanitize_db_error(e)
        logger.error("Failed to retrieve doctors for '%s': %s", clean, safe)
        return {
            "status": "error",
            "message": f"Failed to retrieve doctors: {safe}",
            "count": 0,
            "doctors": [],
        }


def list_specialties() -> Dict[str, Any]:
    """Lists every active clinical department patients can book into.

    Returns:
        Structured result containing the hospital's bookable specialties.
    """
    try:
        with get_db() as db:
            cursor = db[Collections.SPECIALTIES].find(
                {"isActive": True},
                {
                    "slug": 1,
                    "name": 1,
                    "tagline": 1,
                    "description": 1,
                    "commonConditions": 1,
                    "consultationFee": 1,
                    "location": 1,
                    "displayOrder": 1,
                },
            ).sort("displayOrder", 1)

            specialties = [
                {
                    "slug": s.get("slug"),
                    "name": s.get("name"),
                    "tagline": s.get("tagline") or "",
                    "description": s.get("description") or "",
                    "common_conditions": s.get("commonConditions") or [],
                    "consultation_fee_inr": s.get("consultationFee") or 0,
                    "location": s.get("location") or "",
                }
                for s in cursor
            ]
            return {
                "status": "success",
                "count": len(specialties),
                "specialties": specialties,
            }
    except Exception as e:
        safe = sanitize_db_error(e)
        logger.error("Failed to list specialties: %s", safe)
        return {
            "status": "error",
            "message": f"Failed to list specialties: {safe}",
            "count": 0,
            "specialties": [],
        }


def get_doctor_details(doctor_id: str) -> Dict[str, Any]:
    """Retrieves detailed information for a specific doctor.

    Args:
        doctor_id: The doctor's identifier, or their name (e.g. 'Dr. Ananya Sharma').

    Returns:
        Structured doctor profile if found.
    """
    if not doctor_id or not str(doctor_id).strip():
        return {"status": "error", "message": "Doctor ID is required."}

    clean = str(doctor_id).strip()
    try:
        with get_db() as db:
            doctor = resolve_doctor(db, clean)
            if not doctor:
                return {
                    "status": "not_found",
                    "message": f"Doctor '{clean}' does not exist.",
                }
            return {"status": "success", "doctor": doctor_view(doctor)}
    except Exception as e:
        safe = sanitize_db_error(e)
        logger.error("Failed to retrieve doctor '%s': %s", clean, safe)
        return {"status": "error", "message": f"Failed to retrieve doctor details: {safe}"}


def get_available_doctor_slots(
    doctor_id: Optional[str] = None,
    specialty: Optional[str] = None,
    slot_date: Optional[str] = None,
) -> Dict[str, Any]:
    """Finds available appointment slots filtered by doctor, specialty, and/or date.

    Slots are computed live from each doctor's clinic hours minus the
    appointments already on the books, so a slot returned here was genuinely
    free at the moment of the call.

    Args:
        doctor_id: Optional doctor identifier.
        specialty: Optional specialty (e.g. 'Cardiology').
        slot_date: Optional date string in 'YYYY-MM-DD' format. When omitted,
                   the next few clinic days are searched.

    Returns:
        Structured result with matching available slots.
    """
    target_date: Optional[datetime.datetime] = None
    if slot_date and str(slot_date).strip():
        target_date = parse_date(slot_date)
        if target_date is None:
            return {
                "status": "error",
                "message": f"Invalid date format '{slot_date}'. Expected 'YYYY-MM-DD'.",
                "count": 0,
                "slots": [],
            }

    try:
        with get_db() as db:
            doctors: List[Dict[str, Any]] = []

            if doctor_id and str(doctor_id).strip():
                doctor = resolve_doctor(db, doctor_id)
                if not doctor:
                    return {
                        "status": "not_found",
                        "message": f"Doctor '{doctor_id}' does not exist.",
                        "count": 0,
                        "slots": [],
                    }
                doctors = [doctor]
            else:
                query: Dict[str, Any] = {
                    "role": "physician",
                    "isActive": True,
                    "doctorProfile.isBookable": True,
                }
                if specialty and str(specialty).strip():
                    pattern = {"$regex": re.escape(str(specialty).strip()), "$options": "i"}
                    query["$or"] = [
                        {"specialization": pattern},
                        {"department": pattern},
                        {"doctorProfile.specialty": pattern},
                    ]
                doctors = list(db[Collections.USERS].find(query, {"passwordHash": 0}))

            if not doctors:
                return {
                    "status": "success",
                    "count": 0,
                    "slots": [],
                    "message": "No doctors match that search.",
                }

            all_slots: List[Dict[str, Any]] = []
            for doctor in doctors:
                view = doctor_view(doctor)
                if target_date is not None:
                    dates = [target_date]
                else:
                    dates = upcoming_clinic_days(view["available_days"], count=3)

                for date in dates:
                    computed = compute_slots(db, doctor, date)
                    for slot in computed["slots"]:
                        if not slot["available"]:
                            continue
                        all_slots.append(
                            {
                                **slot,
                                "doctor_name": view["doctor_name"],
                                "specialization": view["specialization"],
                                "department": view["department"],
                                "consultation_fee_inr": view["consultation_fee_inr"],
                            }
                        )
                    if len(all_slots) >= MAX_SLOTS * 4:
                        break

            all_slots.sort(key=lambda s: (s["slot_date"], s["start_time"]))
            limited = all_slots[:MAX_SLOTS]

            return {
                "status": "success",
                "count": len(limited),
                "total_found": len(all_slots),
                "slots": limited,
            }
    except Exception as e:
        safe = sanitize_db_error(e)
        logger.error("Failed to retrieve slots: %s", safe)
        return {
            "status": "error",
            "message": f"Failed to retrieve available slots: {safe}",
            "count": 0,
            "slots": [],
        }


def check_slot_availability(slot_id: str) -> Dict[str, Any]:
    """Checks the current real-time availability of a specific slot.

    Args:
        slot_id: The slot identifier returned by get_available_doctor_slots.

    Returns:
        Dictionary indicating whether the slot is available and its full details.
    """
    if not slot_id or not str(slot_id).strip():
        return {"status": "error", "message": "Slot ID is required.", "is_available": False}

    parsed = parse_slot_id(slot_id)
    if not parsed:
        return {
            "status": "error",
            "is_available": False,
            "message": (
                f"Slot identifier '{slot_id}' is not valid. Use a slot_id returned by "
                "get_available_doctor_slots."
            ),
        }

    try:
        with get_db() as db:
            doctor = resolve_doctor(db, parsed["doctor_id"])
            if not doctor:
                return {
                    "status": "not_found",
                    "is_available": False,
                    "message": f"Doctor '{parsed['doctor_id']}' does not exist.",
                }

            view = doctor_view(doctor)
            computed = compute_slots(db, doctor, parsed["date"], include_booked=True)
            if computed.get("reason"):
                return {
                    "status": "not_found",
                    "is_available": False,
                    "slot_status": "Unavailable",
                    "message": computed["reason"],
                }

            match = next(
                (s for s in computed["slots"] if s["start_time"] == parsed["time"]), None
            )
            if not match:
                return {
                    "status": "not_found",
                    "is_available": False,
                    "message": (
                        f"{parsed['time']} is not a clinic slot for "
                        f"{view['doctor_name']} on {iso_date(parsed['date'])}."
                    ),
                }

            return {
                "status": "success",
                "is_available": match["available"],
                "slot_status": match["slot_status"],
                "slot": {
                    **match,
                    "doctor_name": view["doctor_name"],
                    "specialization": view["specialization"],
                    "consultation_fee_inr": view["consultation_fee_inr"],
                },
            }
    except Exception as e:
        safe = sanitize_db_error(e)
        logger.error("Failed to check slot '%s': %s", slot_id, safe)
        return {
            "status": "error",
            "is_available": False,
            "message": f"Failed to check slot availability: {safe}",
        }


def get_appointment_details(
    appointment_id: str,
    authenticated_patient_id: str,
) -> Dict[str, Any]:
    """Retrieves details of an existing appointment, strictly enforcing patient authorization.

    Args:
        appointment_id: The appointment identifier (e.g. 'APT-20261031-0001').
        authenticated_patient_id: The verified identity of the requesting patient.

    Returns:
        Structured appointment details if authorized, or error/not found.
    """
    if not appointment_id or not str(appointment_id).strip():
        return {"status": "error", "message": "Appointment ID is required."}
    if not authenticated_patient_id or not str(authenticated_patient_id).strip():
        return {"status": "unauthorized", "message": "Authenticated patient ID is required."}

    clean_apt = str(appointment_id).strip()
    clean_patient = str(authenticated_patient_id).strip()

    try:
        with get_db() as db:
            appointment = db[Collections.APPOINTMENTS].find_one({"appointmentId": clean_apt})
            if not appointment:
                appointment = db[Collections.APPOINTMENTS].find_one(
                    {"appointmentId": {"$regex": f"^{re.escape(clean_apt)}$", "$options": "i"}}
                )
            if not appointment:
                return {
                    "status": "not_found",
                    "message": f"Appointment '{clean_apt}' not found.",
                }

            caller = resolve_patient(db, clean_patient)
            if not caller or caller.get("_id") != appointment.get("patientId"):
                return {
                    "status": "forbidden",
                    "message": "Access denied. You are not authorized to view this appointment.",
                }

            doctor = db[Collections.USERS].find_one(
                {"_id": appointment.get("doctorId")}, {"passwordHash": 0}
            )
            doctor_info = doctor_view(doctor) if doctor else {}
            scheduled = appointment.get("scheduledDate")

            return {
                "status": "success",
                "appointment": {
                    "appointment_id": appointment.get("appointmentId"),
                    "patient_id": caller.get("patientId"),
                    "doctor_id": str(appointment.get("doctorId")),
                    "doctor_name": doctor_info.get("doctor_name", "Unknown"),
                    "specialization": doctor_info.get("specialization")
                    or appointment.get("specialty")
                    or "",
                    "appointment_date": iso_date(scheduled)
                    if isinstance(scheduled, datetime.datetime)
                    else serialize(scheduled),
                    "start_time": appointment.get("scheduledTime"),
                    "duration_minutes": appointment.get("duration"),
                    "appointment_type": appointment.get("type"),
                    "booking_status": appointment.get("status"),
                    "location": appointment.get("location") or "",
                    "reason": appointment.get("reason") or "",
                    "is_active": appointment.get("status") in ACTIVE_APPOINTMENT_STATUSES,
                },
            }
    except Exception as e:
        safe = sanitize_db_error(e)
        logger.error("Failed to retrieve appointment '%s': %s", clean_apt, safe)
        return {"status": "error", "message": f"Failed to retrieve appointment: {safe}"}


def get_patient_appointments(authenticated_patient_id: str) -> Dict[str, Any]:
    """Lists the authenticated patient's upcoming appointments.

    Args:
        authenticated_patient_id: The verified identity of the requesting patient.

    Returns:
        Structured list of the patient's scheduled and confirmed appointments.
    """
    if not authenticated_patient_id or not str(authenticated_patient_id).strip():
        return {
            "status": "unauthorized",
            "message": "Authenticated patient ID is required.",
            "count": 0,
            "appointments": [],
        }

    try:
        with get_db() as db:
            patient = resolve_patient(db, authenticated_patient_id)
            if not patient:
                return {
                    "status": "not_found",
                    "message": "No patient record matches that identity.",
                    "count": 0,
                    "appointments": [],
                }

            cursor = (
                db[Collections.APPOINTMENTS]
                .find(
                    {
                        "patientId": patient["_id"],
                        "status": {"$in": ACTIVE_APPOINTMENT_STATUSES},
                    }
                )
                .sort([("scheduledDate", 1), ("scheduledTime", 1)])
                .limit(25)
            )

            appointments = []
            for appt in cursor:
                doctor = db[Collections.USERS].find_one(
                    {"_id": appt.get("doctorId")}, {"passwordHash": 0}
                )
                view = doctor_view(doctor) if doctor else {}
                scheduled = appt.get("scheduledDate")
                appointments.append(
                    {
                        "appointment_id": appt.get("appointmentId"),
                        "doctor_name": view.get("doctor_name", "Unknown"),
                        "specialization": view.get("specialization")
                        or appt.get("specialty")
                        or "",
                        "appointment_date": iso_date(scheduled)
                        if isinstance(scheduled, datetime.datetime)
                        else serialize(scheduled),
                        "start_time": appt.get("scheduledTime"),
                        "booking_status": appt.get("status"),
                        "location": appt.get("location") or "",
                    }
                )

            return {
                "status": "success",
                "patient_id": patient.get("patientId"),
                "count": len(appointments),
                "appointments": appointments,
            }
    except Exception as e:
        safe = sanitize_db_error(e)
        logger.error("Failed to list appointments: %s", safe)
        return {
            "status": "error",
            "message": f"Failed to list appointments: {safe}",
            "count": 0,
            "appointments": [],
        }
