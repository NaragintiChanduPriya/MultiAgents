"""Medication record lookup, backed by MongoDB.

In Mongo a `medications` document is one *list* per (admission, listType) with
the individual drugs embedded in a `medications[]` array. Each embedded item
carries its own ObjectId, so that id is the medication record identifier the
agent works with. A `<listDocId>:<index>` form is accepted too, because that
is what a caller paging through a list naturally has to hand.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional

from agents.tools.common import resolve_admission, resolve_patient
from database import (
    Collections,
    get_db,
    sanitize_db_error,
    serialize,
    to_object_id,
)

logger = logging.getLogger(__name__)

# How the stored listType reads to a clinician.
LIST_TYPE_LABEL = {
    "pre_admission": "Pre-admission",
    "inpatient": "Inpatient",
    "discharge": "Discharge",
    "prescription": "Prescribed / dispensed",
}


def _record_view(
    item: Dict[str, Any],
    parent: Dict[str, Any],
    patient_id: Optional[str] = None,
    admission_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Projects an embedded medication item into the flat record shape."""
    dosage = item.get("dosage") or ""
    # "10 mg" → value 10, unit "mg". Best effort only; the raw string is kept.
    strength_value: Optional[float] = None
    strength_unit = ""
    match = re.match(r"^\s*([\d.]+)\s*([a-zA-Z/%]+)", dosage)
    if match:
        try:
            strength_value = float(match.group(1))
        except ValueError:
            strength_value = None
        strength_unit = match.group(2)

    # A prescription has no listType — it is an order, not one of the three
    # reconciliation lists. Label it for what it is.
    list_type = parent.get("listType") or ("prescription" if parent.get("prescriptionId") else "")
    frequency = item.get("frequency") or ""
    notes = item.get("notes")

    instructions = " ".join(p for p in [dosage, frequency] if p).strip()
    if notes:
        instructions = f"{instructions} — {notes}".strip(" —")

    return {
        "medication_record_id": str(item.get("_id")) if item.get("_id") else None,
        "admission_id": admission_id,
        "patient_id": patient_id,
        "medication_name": item.get("drugName") or "",
        "generic_name": item.get("genericName") or "",
        "drug_class": item.get("drugClass") or "",
        "strength": dosage,
        "strength_value": strength_value,
        "strength_unit": strength_unit,
        "route": item.get("route") or "",
        "frequency": frequency,
        "medication_type": LIST_TYPE_LABEL.get(list_type, list_type),
        "list_type": list_type,
        "dose_instructions": instructions or "As directed",
        "medication_status": "Active" if item.get("isActive", True) else "Stopped",
        "start_date": serialize(item.get("startDate")),
        "end_date": serialize(item.get("endDate")),
        "notes": notes,
    }


def _find_item(db, clean_id: str) -> Optional[tuple[Dict[str, Any], Dict[str, Any]]]:
    """Locates an embedded medication item by its id. Returns (item, parent)."""
    # Form 1: the embedded item's own ObjectId.
    oid = to_object_id(clean_id)
    if oid:
        parent = db[Collections.MEDICATIONS].find_one({"medications._id": oid})
        if parent:
            for item in parent.get("medications") or []:
                if item.get("_id") == oid:
                    return item, parent

    # Form 2: "<listDocumentId>:<index>".
    if ":" in clean_id:
        doc_part, _, index_part = clean_id.rpartition(":")
        doc_oid = to_object_id(doc_part)
        if doc_oid is not None and index_part.isdigit():
            parent = db[Collections.MEDICATIONS].find_one({"_id": doc_oid})
            if parent:
                items = parent.get("medications") or []
                index = int(index_part)
                if 0 <= index < len(items):
                    return items[index], parent

    # Form 3: a line on a prescription. The pharmacy hands a patient a
    # prescribed item, not a reconciliation-list entry, so "explain this
    # medicine" at the counter resolves here.
    if oid:
        rx = db[Collections.PRESCRIPTIONS].find_one({"items._id": oid})
        if rx:
            for item in rx.get("items") or []:
                if item.get("_id") == oid:
                    return _prescription_item_as_record(item), rx
    return None


def _prescription_item_as_record(item: Dict[str, Any]) -> Dict[str, Any]:
    """Maps a prescribed line onto the medication-item shape `_record_view` reads.

    The two collections use different field names for the same clinical facts
    (`durationDays`/`instructions` versus `notes`), so they are translated here
    rather than teaching every caller about both shapes.
    """
    duration = item.get("durationDays") or 0
    notes = item.get("instructions") or None
    if duration:
        notes = f"{notes} (for {duration} days)" if notes else f"Course length: {duration} days"
    if item.get("substitutedWith"):
        supplied = f"Pharmacy supplied {item['substitutedWith']}"
        notes = f"{notes}. {supplied}" if notes else supplied

    return {
        "_id": item.get("_id"),
        "drugName": item.get("drugName"),
        "genericName": item.get("genericName"),
        "drugClass": item.get("drugClass"),
        "dosage": item.get("dosage"),
        "frequency": item.get("frequency"),
        "route": item.get("route"),
        "isActive": True,
        "notes": notes,
        "startDate": None,
        "endDate": None,
    }


def get_medication_by_record_id(medication_record_id: str) -> Dict[str, Any]:
    """Retrieves a single verified medication record by its unique identifier.

    Args:
        medication_record_id: The medication record identifier, as returned by
            list_medications_for_patient.

    Returns:
        A structured dictionary containing:
            - status: 'success', 'not_found', or 'error'
            - found: True if the record exists, False otherwise
            - message: Descriptive status message
            - record: The serialized medication record dictionary (or None)
    """
    if not medication_record_id or not str(medication_record_id).strip():
        return {
            "status": "error",
            "found": False,
            "message": "medication_record_id parameter is required and cannot be empty.",
            "record": None,
        }

    clean_id = str(medication_record_id).strip()

    try:
        with get_db() as db:
            found = _find_item(db, clean_id)
            if not found:
                return {
                    "status": "not_found",
                    "found": False,
                    "message": f"No medication record found with identifier '{clean_id}'.",
                    "record": None,
                }

            item, parent = found

            patient = db[Collections.PATIENTS].find_one(
                {"_id": parent.get("patientId")}, {"patientId": 1}
            )
            admission = db[Collections.ADMISSIONS].find_one(
                {"_id": parent.get("admissionId")}, {"admissionId": 1}
            )

            record = _record_view(
                item,
                parent,
                patient_id=patient.get("patientId") if patient else None,
                admission_id=admission.get("admissionId") if admission else None,
            )

            return {
                "status": "success",
                "found": True,
                "message": (
                    f"Medication record '{record['medication_record_id']}' retrieved successfully."
                ),
                "record": record,
            }
    except Exception as e:
        safe = sanitize_db_error(e)
        logger.error("Failed to query medication record %s: %s", clean_id, safe)
        return {
            "status": "error",
            "found": False,
            "message": f"Database error retrieving medication record: {safe}",
            "record": None,
        }


def list_medications_for_patient(
    patient_id: str,
    list_type: Optional[str] = None,
) -> Dict[str, Any]:
    """Lists a patient's medication records with the ids needed to explain them.

    Args:
        patient_id: The patient identifier (e.g. 'PAT-20261031-0001').
        list_type: Optional filter — 'pre_admission', 'inpatient' or 'discharge'.

    Returns:
        Structured result containing each medication record and its identifier.
    """
    if not patient_id or not str(patient_id).strip():
        return {
            "status": "error",
            "message": "patient_id parameter is required.",
            "count": 0,
            "records": [],
        }

    try:
        with get_db() as db:
            patient = resolve_patient(db, patient_id)
            if not patient:
                return {
                    "status": "not_found",
                    "message": f"No patient found with identifier '{patient_id}'.",
                    "count": 0,
                    "records": [],
                }

            query: Dict[str, Any] = {"patientId": patient["_id"]}
            if list_type and str(list_type).strip():
                query["listType"] = str(list_type).strip()

            records: List[Dict[str, Any]] = []
            admission_cache: Dict[Any, Optional[str]] = {}

            for parent in db[Collections.MEDICATIONS].find(query):
                admission_key = parent.get("admissionId")
                if admission_key not in admission_cache:
                    admission = db[Collections.ADMISSIONS].find_one(
                        {"_id": admission_key}, {"admissionId": 1}
                    )
                    admission_cache[admission_key] = (
                        admission.get("admissionId") if admission else None
                    )
                for item in parent.get("medications") or []:
                    records.append(
                        _record_view(
                            item,
                            parent,
                            patient_id=patient.get("patientId"),
                            admission_id=admission_cache[admission_key],
                        )
                    )

            records.sort(key=lambda r: (r.get("list_type") or "", r.get("medication_name") or ""))

            return {
                "status": "success",
                "patient_id": patient.get("patientId"),
                "count": len(records),
                "records": records,
            }
    except Exception as e:
        safe = sanitize_db_error(e)
        logger.error("Failed to list medications for %s: %s", patient_id, safe)
        return {
            "status": "error",
            "message": f"Database error listing medications: {safe}",
            "count": 0,
            "records": [],
        }


def list_medications_for_admission(
    admission_id: str,
    list_type: Optional[str] = None,
) -> Dict[str, Any]:
    """Lists every medication record attached to one admission.

    Args:
        admission_id: The admission identifier (e.g. 'ADM-20261031-0001').
        list_type: Optional filter — 'pre_admission', 'inpatient' or 'discharge'.

    Returns:
        Structured result containing each medication record and its identifier.
    """
    if not admission_id or not str(admission_id).strip():
        return {
            "status": "error",
            "message": "admission_id parameter is required.",
            "count": 0,
            "records": [],
        }

    try:
        with get_db() as db:
            admission = resolve_admission(db, admission_id)
            if not admission:
                return {
                    "status": "not_found",
                    "message": f"No admission found with identifier '{admission_id}'.",
                    "count": 0,
                    "records": [],
                }

            patient = db[Collections.PATIENTS].find_one(
                {"_id": admission.get("patientId")}, {"patientId": 1}
            )

            query: Dict[str, Any] = {"admissionId": admission["_id"]}
            if list_type and str(list_type).strip():
                query["listType"] = str(list_type).strip()

            records: List[Dict[str, Any]] = []
            for parent in db[Collections.MEDICATIONS].find(query):
                for item in parent.get("medications") or []:
                    records.append(
                        _record_view(
                            item,
                            parent,
                            patient_id=patient.get("patientId") if patient else None,
                            admission_id=admission.get("admissionId"),
                        )
                    )

            records.sort(key=lambda r: (r.get("list_type") or "", r.get("medication_name") or ""))

            return {
                "status": "success",
                "admission_id": admission.get("admissionId"),
                "count": len(records),
                "records": records,
            }
    except Exception as e:
        safe = sanitize_db_error(e)
        logger.error("Failed to list medications for admission %s: %s", admission_id, safe)
        return {
            "status": "error",
            "message": f"Database error listing medications: {safe}",
            "count": 0,
            "records": [],
        }


def format_medication_record_for_prompt(record: Dict[str, Any]) -> str:
    """Formats a verified medication record into clean markdown for LLM explanation."""
    if not record:
        return "No medication record provided."

    strength = record.get("strength") or "N/A"
    value = record.get("strength_value")
    unit = record.get("strength_unit") or ""
    if value is not None:
        strength = f"{strength} ({value} {unit})".strip()

    lines = [
        f"- **Medication Record ID**: {record.get('medication_record_id', 'N/A')}",
        f"- **Medication Name**: {record.get('medication_name', 'N/A')}",
        f"- **Generic Name**: {record.get('generic_name') or 'N/A'}",
        f"- **Drug Class**: {record.get('drug_class') or 'N/A'}",
        f"- **Recorded Strength**: {strength}",
        f"- **Frequency**: {record.get('frequency') or 'N/A'}",
        f"- **Route**: {record.get('route', 'N/A')}",
        f"- **Medication Type**: {record.get('medication_type', 'N/A')}",
        f"- **Dose Instructions**: {record.get('dose_instructions', 'N/A')}",
        f"- **Status**: {record.get('medication_status', 'N/A')}",
        f"- **Admission ID**: {record.get('admission_id') or 'N/A'}",
    ]
    if record.get("notes"):
        lines.append(f"- **Clinical Notes**: {record['notes']}")
    return "\n".join(lines)
