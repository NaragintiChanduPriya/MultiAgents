"""Prescription lookup for the Medication Agent.

A prescription is what the pharmacy actually hands over, so the counselling
sheet a patient leaves with has to be written from it — not from the
reconciliation lists, which describe what the patient was on, and not from
whatever drug names happen to appear in the conversation.

The agent reads a prescription by id and nothing else. It cannot be talked
into counselling on a drug that was never dispensed, because there is no tool
that accepts a drug name.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional

from agents.tools.common import resolve_patient
from database import Collections, get_db, sanitize_db_error, serialize, to_object_id

logger = logging.getLogger(__name__)

STATUS_LABEL = {
    "draft": "Draft — not yet sent to the pharmacy",
    "sent_to_pharmacy": "With the pharmacy, awaiting checking",
    "preparing": "Being prepared at the pharmacy",
    "ready": "Ready for collection",
    "dispensed": "Dispensed to the patient",
    "cancelled": "Cancelled",
}


def _item_view(item: Dict[str, Any]) -> Dict[str, Any]:
    """Projects one prescribed line into the shape the agent explains."""
    dosage = item.get("dosage") or ""
    strength_value: Optional[float] = None
    strength_unit = ""
    match = re.match(r"^\s*([\d.]+)\s*([a-zA-Z/%]+)", dosage)
    if match:
        try:
            strength_value = float(match.group(1))
        except ValueError:
            strength_value = None
        strength_unit = match.group(2)

    duration = item.get("durationDays") or 0
    return {
        "item_id": str(item.get("_id")) if item.get("_id") else None,
        "medication_name": item.get("drugName") or "",
        "generic_name": item.get("genericName") or "",
        "drug_class": item.get("drugClass") or "",
        "strength": dosage,
        "strength_value": strength_value,
        "strength_unit": strength_unit,
        "frequency": item.get("frequency") or "",
        "route": item.get("route") or "",
        "duration_days": duration,
        "duration_text": f"{duration} days" if duration else "Ongoing until reviewed",
        "quantity": item.get("quantity") or 0,
        "instructions": item.get("instructions") or "",
        "substitution_allowed": bool(item.get("substitutionAllowed", True)),
        "dispensed": bool(item.get("isDispensed", False)),
        "dispensed_quantity": item.get("dispensedQuantity") or 0,
        "substituted_with": item.get("substitutedWith"),
        "pharmacy_note": item.get("pharmacyNote"),
    }


def _empty(status: str, message: str) -> Dict[str, Any]:
    return {
        "status": status,
        "found": False,
        "message": message,
        "prescription": None,
        "items": [],
        "safety_flags": [],
        "patient": None,
    }


def get_prescription_by_id(prescription_id: str) -> Dict[str, Any]:
    """Retrieves a verified prescription and every medicine on it.

    Args:
        prescription_id: The prescription identifier (e.g. 'RX-20261101-0001').

    Returns:
        Structured dictionary containing:
            - status: 'success', 'not_found', or 'error'
            - found: True if the prescription exists
            - message: Descriptive status message
            - prescription: Header details (status, prescriber, diagnosis, dates)
            - items: Each prescribed medicine
            - safety_flags: Pharmacist-visible warnings raised by the rules engine
            - patient: Patient identity and documented allergies
    """
    if not prescription_id or not str(prescription_id).strip():
        return _empty("error", "prescription_id parameter is required and cannot be empty.")

    clean_id = str(prescription_id).strip()

    try:
        with get_db() as db:
            doc = db[Collections.PRESCRIPTIONS].find_one({"prescriptionId": clean_id})
            if not doc:
                doc = db[Collections.PRESCRIPTIONS].find_one(
                    {"prescriptionId": {"$regex": f"^{re.escape(clean_id)}$", "$options": "i"}}
                )
            if not doc:
                oid = to_object_id(clean_id)
                if oid:
                    doc = db[Collections.PRESCRIPTIONS].find_one({"_id": oid})
            if not doc:
                return _empty(
                    "not_found", f"No prescription found with identifier '{clean_id}'."
                )

            items = [_item_view(i) for i in (doc.get("items") or [])]

            safety = doc.get("safety") or {}
            flags = [
                {
                    "flag_id": f.get("flagId"),
                    "type": f.get("type"),
                    "severity": f.get("severity"),
                    "drug_a": f.get("drugA"),
                    "drug_b": f.get("drugB"),
                    "description": f.get("description"),
                    "recommendation": f.get("recommendation"),
                    "acknowledged": bool(f.get("acknowledged")),
                }
                for f in (safety.get("flags") or [])
            ]

            prescriber = db[Collections.USERS].find_one(
                {"_id": doc.get("prescribedBy")},
                {"firstName": 1, "lastName": 1, "department": 1},
            )
            prescriber_name = (
                f"Dr. {prescriber.get('firstName', '')} {prescriber.get('lastName', '')}".strip()
                if prescriber
                else "Unknown"
            )

            patient_doc = db[Collections.PATIENTS].find_one({"_id": doc.get("patientId")})
            patient_info = None
            if patient_doc:
                demo = patient_doc.get("demographics") or {}
                history = patient_doc.get("medicalHistory") or {}
                allergies = history.get("allergies") or []
                patient_info = {
                    "patient_id": patient_doc.get("patientId"),
                    "first_name": demo.get("firstName") or "",
                    "last_name": demo.get("lastName") or "",
                    "preferred_language": demo.get("preferredLanguage") or "English",
                    # How plainly to write. A basic reading level is not a
                    # reason to leave anything out, only to say it simply.
                    "literacy_level": demo.get("literacyLevel") or "intermediate",
                    "allergies": ", ".join(
                        a.get("substance", "") for a in allergies if a.get("substance")
                    )
                    or "None documented",
                }

            pharmacy = doc.get("pharmacy") or {}
            status_value = doc.get("status") or ""

            prescription = {
                "prescription_id": doc.get("prescriptionId"),
                "status": status_value,
                "status_text": STATUS_LABEL.get(status_value, status_value),
                "priority": doc.get("priority") or "routine",
                "diagnosis": doc.get("diagnosis") or "",
                "notes": doc.get("notes") or "",
                "prescriber": prescriber_name,
                "department": (prescriber or {}).get("department") or "",
                "prescribed_at": serialize(doc.get("prescribedAt")),
                "dispensed_at": serialize(pharmacy.get("dispensedAt")),
                "collected_by": pharmacy.get("collectedBy") or "",
                "pharmacy_notes": pharmacy.get("notes") or "",
                "item_count": len(items),
            }

            return {
                "status": "success",
                "found": True,
                "message": f"Prescription '{doc.get('prescriptionId')}' retrieved successfully.",
                "prescription": prescription,
                "items": items,
                "safety_flags": flags,
                "patient": patient_info,
            }

    except Exception as e:
        safe = sanitize_db_error(e)
        logger.error("Failed to query prescription %s: %s", clean_id, safe)
        return _empty("error", f"Database error retrieving prescription: {safe}")


def list_prescriptions_for_patient(
    patient_id: str,
    status: Optional[str] = None,
) -> Dict[str, Any]:
    """Lists a patient's prescriptions, most recent first.

    Args:
        patient_id: The patient identifier (e.g. 'PAT-20261031-0001').
        status: Optional filter — 'draft', 'sent_to_pharmacy', 'preparing',
            'ready', 'dispensed' or 'cancelled'.

    Returns:
        Structured result containing each prescription and its identifier.
    """
    if not patient_id or not str(patient_id).strip():
        return {
            "status": "error",
            "message": "patient_id parameter is required.",
            "count": 0,
            "prescriptions": [],
        }

    try:
        with get_db() as db:
            patient = resolve_patient(db, patient_id)
            if not patient:
                return {
                    "status": "not_found",
                    "message": f"No patient found with identifier '{patient_id}'.",
                    "count": 0,
                    "prescriptions": [],
                }

            query: Dict[str, Any] = {"patientId": patient["_id"]}
            if status and str(status).strip():
                query["status"] = str(status).strip()

            results: List[Dict[str, Any]] = []
            for doc in (
                db[Collections.PRESCRIPTIONS]
                .find(query)
                .sort("prescribedAt", -1)
                .limit(25)
            ):
                status_value = doc.get("status") or ""
                results.append(
                    {
                        "prescription_id": doc.get("prescriptionId"),
                        "status": status_value,
                        "status_text": STATUS_LABEL.get(status_value, status_value),
                        "priority": doc.get("priority") or "routine",
                        "diagnosis": doc.get("diagnosis") or "",
                        "prescribed_at": serialize(doc.get("prescribedAt")),
                        "item_count": len(doc.get("items") or []),
                        "medicines": [
                            i.get("drugName") for i in (doc.get("items") or []) if i.get("drugName")
                        ],
                    }
                )

            return {
                "status": "success",
                "patient_id": patient.get("patientId"),
                "count": len(results),
                "prescriptions": results,
            }
    except Exception as e:
        safe = sanitize_db_error(e)
        logger.error("Failed to list prescriptions for %s: %s", patient_id, safe)
        return {
            "status": "error",
            "message": f"Database error listing prescriptions: {safe}",
            "count": 0,
            "prescriptions": [],
        }


def format_prescription_for_prompt(data: Dict[str, Any]) -> str:
    """Formats a retrieved prescription into markdown for the counselling prompt."""
    if not data or not data.get("prescription"):
        return "No prescription data available."

    rx = data["prescription"]
    patient = data.get("patient") or {}
    items = data.get("items") or []
    flags = data.get("safety_flags") or []

    lines = [
        "### Prescription",
        f"- Prescription ID: {rx.get('prescription_id', 'N/A')}",
        f"- Status: {rx.get('status_text', 'N/A')}",
        f"- Prescribed by: {rx.get('prescriber', 'N/A')}"
        + (f" ({rx['department']})" if rx.get("department") else ""),
        f"- Prescribed on: {rx.get('prescribed_at', 'N/A')}",
        f"- Reason / diagnosis: {rx.get('diagnosis') or 'Not recorded'}",
    ]
    if rx.get("notes"):
        lines.append(f"- Prescriber's note: {rx['notes']}")

    if patient:
        lines += [
            "",
            "### Patient",
            f"- Name: {patient.get('first_name', '')} {patient.get('last_name', '')}".rstrip(),
            f"- Preferred language: {patient.get('preferred_language', 'English')}",
            f"- Reading level to write for: {patient.get('literacy_level', 'intermediate')}",
            f"- Documented allergies: {patient.get('allergies', 'None documented')}",
        ]

    lines += ["", f"### Medicines dispensed ({len(items)})"]
    if items:
        for index, item in enumerate(items, start=1):
            detail = [
                f"{index}. **{item.get('medication_name', 'Unknown')}**"
                + (f" ({item['generic_name']})" if item.get("generic_name") else ""),
                f"   - Class: {item.get('drug_class') or 'Not recorded'}",
                f"   - Dose: {item.get('strength') or 'Not recorded'}",
                f"   - How often: {item.get('frequency') or 'Not recorded'}",
                f"   - How taken: {item.get('route') or 'Not recorded'}",
                f"   - For how long: {item.get('duration_text', 'Not recorded')}",
            ]
            if item.get("quantity"):
                detail.append(f"   - Quantity supplied: {item['quantity']}")
            if item.get("instructions"):
                detail.append(f"   - Prescriber's instructions: {item['instructions']}")
            if item.get("substituted_with"):
                detail.append(
                    f"   - NOTE: the pharmacy supplied {item['substituted_with']} instead"
                )
            if item.get("pharmacy_note"):
                detail.append(f"   - Pharmacy note: {item['pharmacy_note']}")
            lines.extend(detail)
    else:
        lines.append("- No medicines recorded on this prescription.")

    if flags:
        lines += ["", "### Clinical warnings already reviewed by the pharmacist"]
        for flag in flags:
            pair = f"{flag.get('drug_a')}" + (
                f" + {flag['drug_b']}" if flag.get("drug_b") else ""
            )
            lines.append(
                f"- [{str(flag.get('severity', '')).upper()}] {pair}: {flag.get('description')}"
            )

    return "\n".join(lines)
