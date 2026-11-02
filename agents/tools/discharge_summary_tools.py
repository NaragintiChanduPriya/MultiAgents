"""Discharge package retrieval, backed by MongoDB.

Where PostgreSQL had separate `procedures`, `lab_results` and `clinical_notes`
tables joined on admission_id, Mongo embeds all three inside the admission
document. Vitals remain their own collection. The returned shape is unchanged
so the agent instructions and the response schema did not have to move.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional

from agents.tools.common import patient_view
from agents.tools.medication_tools import _record_view
from database import (
    Collections,
    get_db,
    sanitize_db_error,
    serialize,
    to_object_id,
)

logger = logging.getLogger(__name__)


def _empty(status: str, message: str, missing: List[str]) -> Dict[str, Any]:
    """Builds the uniform empty envelope every failure path returns."""
    return {
        "status": status,
        "found": False,
        "message": message,
        "package": None,
        "admission": None,
        "patient": None,
        "medications": [],
        "procedures": [],
        "lab_results": [],
        "clinical_notes": [],
        "vitals": [],
        "missing_information": missing,
    }


def _package_view(pkg: Dict[str, Any]) -> Dict[str, Any]:
    """Flattens the nested discharge package document into the agent's shape."""
    summary = pkg.get("clinicalSummary") or {}
    recon = pkg.get("medicationReconciliation") or {}
    follow_up = pkg.get("followUpPlan") or {}
    education = pkg.get("patientEducation") or {}
    safety = pkg.get("safetyValidation") or {}
    approvals = pkg.get("approvals") or []

    def review_status(role: str) -> str:
        """Latest action recorded by a given role across all sections."""
        actions = [a for a in approvals if a.get("role") == role]
        if not actions:
            return "Pending"
        latest = actions[-1]
        return str(latest.get("action", "pending")).capitalize()

    recommendations = follow_up.get("recommendations") or []
    follow_up_text = (
        "; ".join(
            f"{r.get('description', '')} ({r.get('timeframe', '')}, {r.get('priority', '')})".strip()
            for r in recommendations
            if r.get("description")
        )
        or "None documented"
    )

    confidences = [
        summary.get("agentConfidence"),
        recon.get("agentConfidence"),
        follow_up.get("agentConfidence"),
        education.get("agentConfidence"),
    ]
    present = [c for c in confidences if isinstance(c, (int, float)) and c > 0]
    confidence_score = round(sum(present) / len(present), 2) if present else None

    return {
        "package_id": pkg.get("packageId"),
        "admission_id": None,  # Filled in by the caller once resolved.
        "initiated_at": serialize(pkg.get("initiatedAt")),
        "ready_for_review_at": serialize(summary.get("generatedAt")),
        "clinical_summary": summary.get("content") or "",
        "clinical_summary_status": summary.get("status") or "pending",
        "clinical_summary_generator": summary.get("generator") or "pending",
        "medication_reconciliation_status": recon.get("status") or "pending",
        "medication_flags_count": len(recon.get("flags") or []),
        "medication_flags": serialize(recon.get("flags") or []),
        "follow_up_plan": follow_up_text,
        "follow_up_recommendations": serialize(recommendations),
        "patient_instructions": education.get("content") or "",
        "patient_instructions_reading_level": education.get("readingLevel") or "",
        "patient_instructions_language": education.get("language") or "",
        "safety_validation_status": (
            "Passed" if safety.get("passed") else "Failed / Pending"
        ),
        "safety_checks": serialize(safety.get("checks") or []),
        "physician_review_status": review_status("physician"),
        "pharmacist_review_status": review_status("pharmacist"),
        "nurse_review_status": review_status("nurse"),
        "package_status": pkg.get("status") or "",
        "confidence_score": confidence_score,
        "completed_at": serialize(pkg.get("completedAt")),
    }


def get_discharge_package_by_id(package_id: str) -> Dict[str, Any]:
    """Retrieves a verified discharge package and all of its related clinical data.

    Args:
        package_id: Unique discharge package identifier (e.g. 'DIS-20261031-0001').

    Returns:
        Structured dictionary containing:
            - status: 'success', 'not_found', or 'error'
            - found: True if the package exists, False otherwise
            - message: Descriptive status message
            - package: The discharge package record
            - admission: Admission details
            - patient: Patient demographics and history
            - medications: Associated medication records
            - procedures: Associated procedures
            - lab_results: Associated lab results
            - clinical_notes: Associated clinical notes
            - vitals: Associated vitals recordings
            - missing_information: List of missing or incomplete fields
    """
    if not package_id or not str(package_id).strip():
        return _empty(
            "error",
            "package_id parameter is required and cannot be empty.",
            ["Missing package_id parameter."],
        )

    clean_id = str(package_id).strip()

    try:
        with get_db() as db:
            pkg = db[Collections.DISCHARGE_PACKAGES].find_one({"packageId": clean_id})
            if not pkg:
                pkg = db[Collections.DISCHARGE_PACKAGES].find_one(
                    {"packageId": {"$regex": f"^{re.escape(clean_id)}$", "$options": "i"}}
                )
            if not pkg:
                oid = to_object_id(clean_id)
                if oid:
                    pkg = db[Collections.DISCHARGE_PACKAGES].find_one({"_id": oid})
            if not pkg:
                return _empty(
                    "not_found",
                    f"No discharge package found with identifier '{clean_id}'.",
                    [f"Package record '{clean_id}' does not exist in the hospital database."],
                )

            package_data = _package_view(pkg)
            missing: List[str] = []

            admission = db[Collections.ADMISSIONS].find_one({"_id": pkg.get("admissionId")})
            admission_data: Optional[Dict[str, Any]] = None
            patient_data: Optional[Dict[str, Any]] = None
            medications: List[Dict[str, Any]] = []
            procedures: List[Dict[str, Any]] = []
            lab_results: List[Dict[str, Any]] = []
            clinical_notes: List[Dict[str, Any]] = []
            vitals: List[Dict[str, Any]] = []

            if not admission:
                missing.append(
                    f"Associated admission record '{pkg.get('admissionId')}' was not found."
                )
            else:
                package_data["admission_id"] = admission.get("admissionId")
                bed = admission.get("bed") or {}
                risk = admission.get("riskAssessment") or {}
                bed_label = " ".join(
                    str(p)
                    for p in [bed.get("ward"), bed.get("roomNumber"), bed.get("bedNumber")]
                    if p
                ).strip()

                admission_data = {
                    "admission_id": admission.get("admissionId"),
                    "patient_id": None,
                    "admission_date": serialize(admission.get("admissionDate")),
                    "discharge_date": serialize(admission.get("actualDischargeDate")),
                    "expected_discharge_date": serialize(
                        admission.get("expectedDischargeDate")
                    ),
                    "admitting_diagnosis": admission.get("admittingDiagnosis") or "",
                    "icd10_code": ", ".join(admission.get("diagnosisCodes") or []),
                    "bed_number": bed_label,
                    "ward": bed.get("ward") or "",
                    "admission_status": admission.get("status") or "",
                    "fall_risk": risk.get("fallRisk") or "",
                    "pressure_injury_risk": risk.get("pressureInjuryRisk") or "",
                    "infection_risk": ", ".join(risk.get("infectionFlags") or []) or "None",
                    "length_of_stay_days": None,
                }

                patient = db[Collections.PATIENTS].find_one({"_id": admission.get("patientId")})
                if patient:
                    patient_data = patient_view(patient)
                    admission_data["patient_id"] = patient_data.get("patient_id")
                else:
                    missing.append("Associated patient record was not found.")

                # Procedures, labs and notes are embedded in the admission.
                for proc in admission.get("procedures") or []:
                    procedures.append(
                        {
                            "procedure_id": str(proc.get("_id")) if proc.get("_id") else None,
                            "admission_id": admission.get("admissionId"),
                            "patient_id": admission_data.get("patient_id"),
                            "recorded_at": serialize(proc.get("date")),
                            "procedure_name": proc.get("name") or "",
                            "procedure_status": "Completed",
                            "notes": proc.get("notes") or "",
                        }
                    )

                for lab in admission.get("labResults") or []:
                    lab_results.append(
                        {
                            "lab_id": str(lab.get("_id")) if lab.get("_id") else None,
                            "admission_id": admission.get("admissionId"),
                            "patient_id": admission_data.get("patient_id"),
                            "recorded_at": serialize(lab.get("resultDate")),
                            "test_name": lab.get("testName") or "",
                            "result_value": lab.get("value") or "",
                            "result_unit": lab.get("unit") or "",
                            "reference_range": lab.get("referenceRange") or "",
                            "abnormal_flag": "ABNORMAL" if lab.get("isAbnormal") else "",
                        }
                    )

                for note in admission.get("clinicalNotes") or []:
                    clinical_notes.append(
                        {
                            "note_id": str(note.get("_id")) if note.get("_id") else None,
                            "admission_id": admission.get("admissionId"),
                            "patient_id": admission_data.get("patient_id"),
                            "recorded_at": serialize(note.get("createdAt")),
                            "note_category": note.get("type") or "",
                            "note_text": note.get("content") or "",
                        }
                    )

                procedures.sort(key=lambda p: p.get("recorded_at") or "")
                lab_results.sort(key=lambda r: r.get("recorded_at") or "")
                clinical_notes.sort(key=lambda n: n.get("recorded_at") or "")

                # Medications for this admission.
                for parent in db[Collections.MEDICATIONS].find(
                    {"admissionId": admission["_id"]}
                ):
                    for item in parent.get("medications") or []:
                        medications.append(
                            _record_view(
                                item,
                                parent,
                                patient_id=admission_data.get("patient_id"),
                                admission_id=admission.get("admissionId"),
                            )
                        )
                medications.sort(
                    key=lambda m: (m.get("list_type") or "", m.get("medication_name") or "")
                )

                # Vitals are their own collection.
                for vital in (
                    db[Collections.VITALS]
                    .find({"admissionId": admission["_id"]})
                    .sort("timestamp", 1)
                ):
                    vitals.append(
                        {
                            "vital_id": str(vital.get("_id")),
                            "admission_id": admission.get("admissionId"),
                            "patient_id": admission_data.get("patient_id"),
                            "recorded_at": serialize(vital.get("timestamp")),
                            "systolic_bp": vital.get("bloodPressureSystolic"),
                            "diastolic_bp": vital.get("bloodPressureDiastolic"),
                            "heart_rate_bpm": vital.get("heartRate"),
                            "temperature_c": vital.get("temperature"),
                            "respiratory_rate": vital.get("respiratoryRate"),
                            "spo2_percent": vital.get("oxygenSaturation"),
                            "pain_score": vital.get("painScore"),
                            "weight_kg": vital.get("weight"),
                        }
                    )

            # Work out what is genuinely absent, so the agent can say so.
            if not package_data.get("completed_at"):
                missing.append(
                    "Discharge package completion timestamp (completed_at) is unrecorded/pending."
                )
            if admission_data and not admission_data.get("discharge_date"):
                missing.append(
                    "Official discharge date is unrecorded (discharge in progress or pending)."
                )
            if not medications:
                missing.append(
                    "No active or discharge medication records found for this episode."
                )
            if not procedures:
                missing.append("No formal procedural records documented during this stay.")
            if not lab_results:
                missing.append(
                    "No diagnostic lab investigation results recorded for this stay."
                )
            if not vitals:
                missing.append("No vitals observations recorded for this admission.")

            return {
                "status": "success",
                "found": True,
                "message": (
                    f"Discharge package '{package_data['package_id']}' retrieved successfully."
                ),
                "package": package_data,
                "admission": admission_data,
                "patient": patient_data,
                "medications": medications,
                "procedures": procedures,
                "lab_results": lab_results,
                "clinical_notes": clinical_notes,
                "vitals": vitals,
                "missing_information": missing,
            }

    except Exception as e:
        safe = sanitize_db_error(e)
        logger.error("Failed to query discharge package %s: %s", clean_id, safe)
        return _empty(
            "error",
            f"Database error retrieving discharge package: {safe}",
            [f"Database error: {safe}"],
        )


def list_discharge_packages(status: Optional[str] = None) -> Dict[str, Any]:
    """Lists discharge packages with the ids needed to generate a report.

    Args:
        status: Optional workflow status filter (e.g. 'pending_review', 'completed').

    Returns:
        Structured result containing each package's identifier and headline details.
    """
    try:
        with get_db() as db:
            query: Dict[str, Any] = {}
            if status and str(status).strip():
                query["status"] = str(status).strip()

            packages: List[Dict[str, Any]] = []
            for pkg in (
                db[Collections.DISCHARGE_PACKAGES].find(query).sort("initiatedAt", -1).limit(50)
            ):
                admission = db[Collections.ADMISSIONS].find_one(
                    {"_id": pkg.get("admissionId")},
                    {"admissionId": 1, "admittingDiagnosis": 1},
                )
                patient = db[Collections.PATIENTS].find_one(
                    {"_id": pkg.get("patientId")},
                    {"patientId": 1, "demographics.firstName": 1, "demographics.lastName": 1},
                )
                demo = (patient or {}).get("demographics") or {}
                packages.append(
                    {
                        "package_id": pkg.get("packageId"),
                        "package_status": pkg.get("status"),
                        "admission_id": (admission or {}).get("admissionId"),
                        "admitting_diagnosis": (admission or {}).get("admittingDiagnosis") or "",
                        "patient_id": (patient or {}).get("patientId"),
                        "patient_name": f"{demo.get('firstName', '')} {demo.get('lastName', '')}".strip(),
                        "initiated_at": serialize(pkg.get("initiatedAt")),
                        "completed_at": serialize(pkg.get("completedAt")),
                    }
                )

            return {"status": "success", "count": len(packages), "packages": packages}
    except Exception as e:
        safe = sanitize_db_error(e)
        logger.error("Failed to list discharge packages: %s", safe)
        return {
            "status": "error",
            "message": f"Database error listing discharge packages: {safe}",
            "count": 0,
            "packages": [],
        }


def format_discharge_package_for_prompt(data: Dict[str, Any]) -> str:
    """Formats a retrieved discharge package and its clinical records as markdown."""
    if not data or not data.get("package"):
        return "No discharge package data available."

    pkg = data["package"]
    adm = data.get("admission") or {}
    pat = data.get("patient") or {}
    meds = data.get("medications") or []
    procs = data.get("procedures") or []
    labs = data.get("lab_results") or []
    notes = data.get("clinical_notes") or []
    vitals = data.get("vitals") or []
    missing = data.get("missing_information") or []

    sections = []

    sections.append(
        "### 1. Discharge Package Overview\n"
        f"- Package ID: {pkg.get('package_id', 'N/A')}\n"
        f"- Admission ID: {pkg.get('admission_id', 'N/A')}\n"
        f"- Status: {pkg.get('package_status', 'N/A')}\n"
        f"- Initiated At: {pkg.get('initiated_at', 'N/A')}\n"
        f"- Ready for Review At: {pkg.get('ready_for_review_at', 'N/A')}\n"
        f"- Completed At: {pkg.get('completed_at') or 'Pending / Unrecorded'}\n"
        f"- Physician Review: {pkg.get('physician_review_status', 'N/A')}\n"
        f"- Pharmacist Review: {pkg.get('pharmacist_review_status', 'N/A')}\n"
        f"- Nurse Review: {pkg.get('nurse_review_status', 'N/A')}\n"
        f"- Medication Reconciliation: {pkg.get('medication_reconciliation_status', 'N/A')} "
        f"(Flags: {pkg.get('medication_flags_count', 0)})\n"
        f"- Safety Validation: {pkg.get('safety_validation_status', 'N/A')}\n"
        f"- Confidence Score: {pkg.get('confidence_score', 'N/A')}"
    )

    if pat:
        sections.append(
            "### 2. Patient Demographics & Baseline\n"
            f"- Patient ID: {pat.get('patient_id', 'N/A')}\n"
            f"- Name: {pat.get('first_name', '')} {pat.get('last_name', '')}".strip() + "\n"
            f"- Gender: {pat.get('gender', 'N/A')}\n"
            f"- Blood Group: {pat.get('blood_group', 'N/A')}\n"
            f"- Known Allergies: {pat.get('allergies', 'None documented')}\n"
            f"- Medical History: {pat.get('medical_history', 'None documented')}"
        )

    if adm:
        sections.append(
            "### 3. Admission & Diagnosis Details\n"
            f"- Admission Date: {adm.get('admission_date', 'N/A')}\n"
            f"- Discharge Date: {adm.get('discharge_date') or 'Pending / Ongoing'}\n"
            f"- Admitting Diagnosis: {adm.get('admitting_diagnosis', 'N/A')}\n"
            f"- ICD-10 Code(s): {adm.get('icd10_code') or 'N/A'}\n"
            f"- Bed: {adm.get('bed_number') or 'N/A'}\n"
            f"- Admission Status: {adm.get('admission_status', 'N/A')}\n"
            f"- Fall Risk: {adm.get('fall_risk', 'N/A')}\n"
            f"- Infection Risk: {adm.get('infection_risk', 'N/A')}"
        )

    sections.append(
        "### 4. Recorded Clinical Summary & Instructions\n"
        f"- Draft Clinical Summary: {pkg.get('clinical_summary') or 'None documented'}\n"
        f"- Follow-up Plan: {pkg.get('follow_up_plan') or 'None documented'}\n"
        f"- Patient Instructions: {pkg.get('patient_instructions') or 'None documented'}"
    )

    if meds:
        lines = ["### 5. Documented Medications"]
        for m in meds:
            lines.append(
                f"- {m.get('medication_name', 'Unknown')} ({m.get('drug_class') or 'N/A'}): "
                f"{m.get('strength', '')} via {m.get('route', '')} | "
                f"Instructions: {m.get('dose_instructions', 'N/A')} | "
                f"Type: {m.get('medication_type', 'N/A')} | Status: {m.get('medication_status', 'N/A')}"
            )
        sections.append("\n".join(lines))
    else:
        sections.append(
            "### 5. Documented Medications\n"
            "- No medication records associated with this admission."
        )

    if procs:
        lines = ["### 6. Documented Procedures"]
        for p in procs:
            lines.append(
                f"- {p.get('procedure_name')} | Status: {p.get('procedure_status')} "
                f"| Recorded: {p.get('recorded_at')}"
            )
        sections.append("\n".join(lines))
    else:
        sections.append(
            "### 6. Documented Procedures\n- No procedural interventions documented."
        )

    if labs:
        lines = ["### 7. Laboratory & Investigation Results"]
        for l in labs:
            flag = f" [FLAG: {l.get('abnormal_flag')}]" if l.get("abnormal_flag") else ""
            lines.append(
                f"- {l.get('test_name')}: {l.get('result_value')} {l.get('result_unit')}{flag} "
                f"(Recorded: {l.get('recorded_at')})"
            )
        sections.append("\n".join(lines))
    else:
        sections.append(
            "### 7. Laboratory & Investigation Results\n"
            "- No laboratory investigations documented."
        )

    if notes:
        lines = ["### 8. Clinical Notes"]
        for n in notes:
            lines.append(
                f"- [{n.get('note_category')}]: {n.get('note_text')} ({n.get('recorded_at')})"
            )
        sections.append("\n".join(lines))

    if vitals:
        lines = ["### 9. Vitals Trend (most recent 5)"]
        for v in vitals[-5:]:
            lines.append(
                f"- {v.get('recorded_at')}: BP {v.get('systolic_bp')}/{v.get('diastolic_bp')}, "
                f"HR {v.get('heart_rate_bpm')}, Temp {v.get('temperature_c')}, "
                f"SpO2 {v.get('spo2_percent')}%, Pain {v.get('pain_score')}/10"
            )
        sections.append("\n".join(lines))

    if missing:
        lines = ["### 10. Identified Missing or Pending Information"]
        for item in missing:
            lines.append(f"- {item}")
        sections.append("\n".join(lines))

    return "\n\n".join(sections)
