"""Shared resolution helpers used by every agent tool.

Two things in this codebase do not map one-to-one onto a table, and both are
handled here so the four agents cannot disagree about them:

1. **Doctors** are `users` with `role: 'physician'` and a `doctorProfile`.
   There is no separate doctors collection.
2. **Slots are computed, never stored.** A doctor's clinic days, hours, slot
   length and buffer generate the grid; booked appointments knock holes in it.
   This mirrors `bookingService.freeSlots` in the Node backend exactly, so the
   website and the agent always offer the same times.
"""

from __future__ import annotations

import datetime
import re
from typing import Any, Dict, List, Optional

from database import Collections, serialize, to_object_id

# A synthesised slot identifier: <doctorObjectIdHex>_<YYYY-MM-DD>_<HHMM>.
# It round-trips, so an agent can hand a slot id back to the booking tool and
# the slot is recomputed and re-verified rather than trusted.
SLOT_ID_RE = re.compile(r"^(?P<doctor>[0-9a-fA-F]{24})_(?P<date>\d{4}-\d{2}-\d{2})_(?P<time>\d{4})$")

DEFAULT_CLINIC_DAYS = [1, 2, 3, 4, 5]
DEFAULT_SLOT_MINUTES = 25
DEFAULT_BUFFER_MINUTES = 5
ACTIVE_APPOINTMENT_STATUSES = ["scheduled", "confirmed"]


# ─── Time helpers ────────────────────────────────────────────────────────────


def to_minutes(hhmm: str) -> int:
    """Converts 'HH:MM' into minutes past midnight."""
    try:
        parts = str(hhmm).split(":")
        return int(parts[0]) * 60 + int(parts[1] if len(parts) > 1 else 0)
    except (ValueError, IndexError):
        return 0


def to_hhmm(minutes: int) -> str:
    """Converts minutes past midnight into 'HH:MM'."""
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def parse_date(value: Any) -> Optional[datetime.datetime]:
    """Parses a 'YYYY-MM-DD' string (or date/datetime) into local midnight."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime.datetime):
        return value.replace(hour=0, minute=0, second=0, microsecond=0)
    if isinstance(value, datetime.date):
        return datetime.datetime(value.year, value.month, value.day)
    text = str(value).strip()
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%Y/%m/%d"):
        try:
            parsed = datetime.datetime.strptime(text[:10], fmt)
            return parsed.replace(hour=0, minute=0, second=0, microsecond=0)
        except ValueError:
            continue
    try:
        parsed = datetime.datetime.fromisoformat(text.replace("Z", "+00:00"))
        return parsed.replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=None)
    except ValueError:
        return None


def iso_date(d: datetime.datetime) -> str:
    """Local calendar day as 'YYYY-MM-DD' — isoformat() would shift timezone."""
    return f"{d.year:04d}-{d.month:02d}-{d.day:02d}"


def day_bounds(d: datetime.datetime) -> tuple[datetime.datetime, datetime.datetime]:
    """Returns the start and end instants of the calendar day `d` falls in."""
    start = d.replace(hour=0, minute=0, second=0, microsecond=0)
    end = d.replace(hour=23, minute=59, second=59, microsecond=999000)
    return start, end


# ─── Slot identifiers ────────────────────────────────────────────────────────


def make_slot_id(doctor_id: Any, date: datetime.datetime, time_str: str) -> str:
    """Builds the deterministic slot identifier for a doctor/date/time."""
    return f"{str(doctor_id)}_{iso_date(date)}_{time_str.replace(':', '')}"


def parse_slot_id(slot_id: str) -> Optional[Dict[str, Any]]:
    """Decodes a slot identifier back into doctor, date and time."""
    if not slot_id:
        return None
    match = SLOT_ID_RE.match(str(slot_id).strip())
    if not match:
        return None
    date = parse_date(match.group("date"))
    if date is None:
        return None
    raw_time = match.group("time")
    return {
        "doctor_id": match.group("doctor"),
        "date": date,
        "time": f"{raw_time[:2]}:{raw_time[2:]}",
    }


# ─── Entity resolution ───────────────────────────────────────────────────────


def resolve_patient(db, identifier: Any) -> Optional[Dict[str, Any]]:
    """Finds a patient by human `patientId` (PAT-YYYYMMDD-NNNN) or ObjectId."""
    if not identifier:
        return None
    text = str(identifier).strip()
    if not text:
        return None

    patient = db[Collections.PATIENTS].find_one({"patientId": text})
    if patient:
        return patient
    # Human IDs are stored upper-case; be forgiving about what a caller sends.
    patient = db[Collections.PATIENTS].find_one(
        {"patientId": {"$regex": f"^{re.escape(text)}$", "$options": "i"}}
    )
    if patient:
        return patient

    oid = to_object_id(text)
    if oid:
        return db[Collections.PATIENTS].find_one({"_id": oid})
    return None


def resolve_doctor(db, identifier: Any) -> Optional[Dict[str, Any]]:
    """Finds a physician by ObjectId, email, or full/partial name."""
    if not identifier:
        return None
    text = str(identifier).strip()
    if not text:
        return None

    oid = to_object_id(text)
    if oid:
        doctor = db[Collections.USERS].find_one({"_id": oid, "role": "physician"})
        if doctor:
            return doctor

    doctor = db[Collections.USERS].find_one(
        {"role": "physician", "email": text.lower()}
    )
    if doctor:
        return doctor

    # "Dr. Ananya Sharma" / "Ananya Sharma" / "Sharma"
    cleaned = re.sub(r"^\s*(dr\.?|doctor)\s+", "", text, flags=re.IGNORECASE).strip()
    parts = [p for p in cleaned.split() if p]
    if parts:
        if len(parts) >= 2:
            match = db[Collections.USERS].find_one(
                {
                    "role": "physician",
                    "firstName": {"$regex": f"^{re.escape(parts[0])}$", "$options": "i"},
                    "lastName": {"$regex": f"^{re.escape(parts[-1])}$", "$options": "i"},
                }
            )
            if match:
                return match
        match = db[Collections.USERS].find_one(
            {
                "role": "physician",
                "$or": [
                    {"firstName": {"$regex": f"^{re.escape(cleaned)}$", "$options": "i"}},
                    {"lastName": {"$regex": f"^{re.escape(cleaned)}$", "$options": "i"}},
                ],
            }
        )
        if match:
            return match
    return None


def resolve_admission(db, identifier: Any) -> Optional[Dict[str, Any]]:
    """Finds an admission by human `admissionId` (ADM-...) or ObjectId."""
    if not identifier:
        return None
    text = str(identifier).strip()
    if not text:
        return None

    admission = db[Collections.ADMISSIONS].find_one({"admissionId": text})
    if admission:
        return admission
    oid = to_object_id(text)
    if oid:
        return db[Collections.ADMISSIONS].find_one({"_id": oid})
    return None


# ─── Presentation ────────────────────────────────────────────────────────────


def doctor_view(doctor: Dict[str, Any]) -> Dict[str, Any]:
    """Projects a user document into the public doctor shape the agents use."""
    profile = doctor.get("doctorProfile") or {}
    first = doctor.get("firstName") or ""
    last = doctor.get("lastName") or ""
    return {
        "doctor_id": str(doctor.get("_id")),
        "doctor_name": f"Dr. {first} {last}".replace("  ", " ").strip(),
        "department": doctor.get("department") or "",
        "specialization": doctor.get("specialization") or profile.get("specialty") or "",
        "specialty_slug": profile.get("specialty") or "",
        "qualifications": profile.get("qualifications") or "",
        "experience_years": profile.get("yearsExperience") or 0,
        "consultation_fee_inr": profile.get("consultationFee") or 0,
        "languages": profile.get("languages") or [],
        "bio": profile.get("bio") or "",
        "available_days": profile.get("availableDays") or DEFAULT_CLINIC_DAYS,
        "clinic_start": profile.get("clinicStart") or "09:00",
        "clinic_end": profile.get("clinicEnd") or "17:00",
        "slot_minutes": profile.get("slotMinutes") or DEFAULT_SLOT_MINUTES,
        "is_active": bool(doctor.get("isActive", True)),
        "is_bookable": bool(profile.get("isBookable", False)),
    }


def patient_view(patient: Dict[str, Any]) -> Dict[str, Any]:
    """Projects a patient document into the flat shape the agents consume."""
    demo = patient.get("demographics") or {}
    history = patient.get("medicalHistory") or {}
    allergies = history.get("allergies") or []
    allergy_text = (
        ", ".join(
            f"{a.get('substance')} ({a.get('severity')}: {a.get('reaction')})".strip()
            for a in allergies
            if a.get("substance")
        )
        or "None documented"
    )
    return {
        "patient_id": patient.get("patientId"),
        "first_name": demo.get("firstName") or "",
        "last_name": demo.get("lastName") or "",
        "date_of_birth": serialize(demo.get("dateOfBirth")),
        "gender": demo.get("gender") or "",
        "blood_group": demo.get("bloodType") or "",
        "preferred_language": demo.get("preferredLanguage") or "English",
        "literacy_level": demo.get("literacyLevel") or "intermediate",
        "allergies": allergy_text,
        "allergy_list": serialize(allergies),
        "has_known_allergy": bool(allergies),
        "medical_history": ", ".join(history.get("conditions") or []) or "None documented",
        "conditions": history.get("conditions") or [],
        "status": patient.get("status") or "",
    }


# ─── Slot computation ────────────────────────────────────────────────────────


def compute_slots(
    db,
    doctor: Dict[str, Any],
    date: datetime.datetime,
    include_booked: bool = False,
    now: Optional[datetime.datetime] = None,
) -> Dict[str, Any]:
    """Computes a doctor's slot grid for one day, minus anything already booked.

    Mirrors `bookingService.freeSlots` in the Node backend so the website and
    the agent never offer different times for the same doctor.
    """
    profile = doctor.get("doctorProfile") or {}
    days = profile.get("availableDays") or DEFAULT_CLINIC_DAYS
    slot_minutes = profile.get("slotMinutes") or DEFAULT_SLOT_MINUTES
    buffer_minutes = profile.get("bufferMinutes")
    if buffer_minutes is None:
        buffer_minutes = DEFAULT_BUFFER_MINUTES
    step = slot_minutes + buffer_minutes

    start, end = day_bounds(date)

    # Python's weekday() is Monday=0; the stored availableDays follow
    # JavaScript's Date.getDay(), where Sunday=0.
    js_weekday = (start.weekday() + 1) % 7
    if days and js_weekday not in days:
        return {
            "slots": [],
            "reason": "The doctor does not hold clinic on this day.",
            "slot_minutes": slot_minutes,
        }

    booked = list(
        db[Collections.APPOINTMENTS].find(
            {
                "doctorId": doctor.get("_id"),
                "scheduledDate": {"$gte": start, "$lte": end},
                "status": {"$in": ACTIVE_APPOINTMENT_STATUSES},
            },
            {"scheduledTime": 1, "duration": 1},
        )
    )

    open_min = to_minutes(profile.get("clinicStart") or "09:00")
    close_min = to_minutes(profile.get("clinicEnd") or "17:00")
    if close_min <= open_min:
        close_min += 24 * 60

    reference = now or datetime.datetime.now()

    slots: List[Dict[str, Any]] = []
    minute = open_min
    while minute + slot_minutes <= close_min:
        wrapped = minute % 1440
        time_str = to_hhmm(wrapped)

        clash = False
        for appt in booked:
            booked_start = to_minutes(appt.get("scheduledTime") or "")
            booked_len = appt.get("duration") or slot_minutes
            if wrapped < booked_start + booked_len and booked_start < wrapped + slot_minutes:
                clash = True
                break

        slot_at = start + datetime.timedelta(minutes=minute)
        in_future = slot_at > reference
        available = (not clash) and in_future

        if available or include_booked:
            slots.append(
                {
                    "slot_id": make_slot_id(doctor.get("_id"), start, time_str),
                    "doctor_id": str(doctor.get("_id")),
                    "slot_date": iso_date(start),
                    "start_time": time_str,
                    "end_time": to_hhmm((wrapped + slot_minutes) % 1440),
                    "slot_status": "Available" if available else "Booked",
                    "available": available,
                }
            )
        minute += step

    return {"slots": slots, "reason": None, "slot_minutes": slot_minutes}


def upcoming_clinic_days(
    days: List[int], count: int = 3, start_from: Optional[datetime.datetime] = None
) -> List[datetime.datetime]:
    """The next `count` days the doctor actually holds clinic, starting tomorrow."""
    out: List[datetime.datetime] = []
    today = (start_from or datetime.datetime.now()).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    i = 1
    while i <= 60 and len(out) < count:
        candidate = today + datetime.timedelta(days=i)
        js_weekday = (candidate.weekday() + 1) % 7
        if not days or js_weekday in days:
            out.append(candidate)
        i += 1
    return out
