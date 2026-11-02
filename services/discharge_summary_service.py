import logging
from typing import Any, Dict, Optional
import uuid

from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

from agents.discharge_summary_report_agent import discharge_summary_report_agent
from agents.tools.discharge_summary_tools import (
    format_discharge_package_for_prompt,
    get_discharge_package_by_id,
)
from schemas.discharge_summary_schema import (
    DischargePackageSourceData,
    DischargeSummaryResponse,
)
from services.llm_retry import collect_agent_text

logger = logging.getLogger(__name__)


class DischargeSummaryService:
    """Coordinates compilation of hospital discharge summary reports using Google ADK Runner.
    
    MongoDB is strictly the source of truth for all clinical and workflow facts.
    The Discharge Summary Report Agent formats and synthesizes the verified records
    into an organized discharge summary report.
    """

    def __init__(self, agent=None, session_service=None):
        self.app_name = "hospital_discharge_summary_app"
        self.agent = agent or discharge_summary_report_agent
        self.session_service = session_service or InMemorySessionService()
        self.runner = Runner(
            agent=self.agent,
            app_name=self.app_name,
            session_service=self.session_service,
        )

    async def get_or_create_session(
        self, user_id: str, session_id: Optional[str] = None
    ) -> str:
        """Retrieves an existing session for the user or creates a new one."""
        if session_id and session_id.strip():
            clean_session_id = session_id.strip()
            existing = await self.session_service.get_session(
                app_name=self.app_name,
                user_id=user_id,
                session_id=clean_session_id,
            )
            if existing:
                return existing.id

        new_session_id = f"ds_sess_{uuid.uuid4().hex[:16]}"
        session = await self.session_service.create_session(
            app_name=self.app_name,
            user_id=user_id,
            session_id=new_session_id,
            state={"user_id": user_id},
        )
        return session.id

    async def generate_discharge_summary(
        self,
        package_id: str,
        authenticated_patient_id: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> DischargeSummaryResponse:
        """Retrieves verified database records and generates a discharge summary report.
        
        Args:
            package_id: Unique discharge package identifier (e.g. 'DP0001').
            authenticated_patient_id: Caller identity for patient privacy & authorization.
            session_id: Optional session identifier for tracking.
            
        Returns:
            DischargeSummaryResponse containing the generated report and verified source data.
        """
        # 1. Validate identifier
        if not package_id or not package_id.strip():
            return DischargeSummaryResponse(
                package_id=package_id or "",
                status="error",
                error="Discharge package identifier is required and cannot be empty.",
                report="",
                source_data=None,
                missing_information=["Missing package_id parameter."],
            )

        clean_id = package_id.strip()

        # 2. Query MongoDB as the absolute source of truth
        tool_result = get_discharge_package_by_id(clean_id)

        # 3. Handle database connection or execution errors
        if tool_result.get("status") == "error":
            return DischargeSummaryResponse(
                package_id=clean_id,
                status="error",
                error=tool_result.get("message", "Database error occurred."),
                report="We are currently unable to generate the discharge summary report due to a database issue. Please try again later.",
                source_data=None,
                missing_information=tool_result.get("missing_information", []),
            )

        # 4. Handle non-existent package
        if not tool_result.get("found") or not tool_result.get("package"):
            return DischargeSummaryResponse(
                package_id=clean_id,
                status="not_found",
                error=f"Discharge package '{clean_id}' was not found in hospital database records.",
                report=f"No discharge package found matching identifier '{clean_id}'. Please check the package identifier and try again.",
                source_data=None,
                missing_information=[f"Package record '{clean_id}' does not exist."],
            )

        package_row = tool_result["package"]
        admission_row = tool_result.get("admission") or {}
        patient_row = tool_result.get("patient") or {}

        # 5. Authorization check: if authenticated user is a patient, verify ownership
        patient_id = patient_row.get("patient_id") or admission_row.get("patient_id")
        if authenticated_patient_id and authenticated_patient_id.strip():
            caller_id = authenticated_patient_id.strip()
            if caller_id.startswith("PAT") and patient_id and caller_id != patient_id:
                logger.warning(
                    "Unauthorized discharge report access attempt: Patient %s tried to access package %s belonging to %s",
                    caller_id,
                    clean_id,
                    patient_id,
                )
                return DischargeSummaryResponse(
                    package_id=clean_id,
                    status="forbidden",
                    error="Access denied. You are not authorized to view discharge records for another patient.",
                    report="",
                    source_data=None,
                    missing_information=["Authorization verification failed."],
                    patient_id=None,
                )

        # 6. Build structured source data
        source_data = DischargePackageSourceData(
            package=package_row,
            admission=admission_row,
            patient=patient_row,
            medications=tool_result.get("medications", []),
            procedures=tool_result.get("procedures", []),
            lab_results=tool_result.get("lab_results", []),
            clinical_notes=tool_result.get("clinical_notes", []),
            vitals=tool_result.get("vitals", []),
        )

        missing_info = tool_result.get("missing_information", [])

        # 7. Generate report via Discharge Summary Report Agent
        user_id = authenticated_patient_id or patient_id or "clinician_guest"
        formatted_prompt = format_discharge_package_for_prompt(tool_result)

        full_prompt = (
            f"Please generate the complete discharge summary report for discharge package ID '{clean_id}' "
            "based strictly on the following verified hospital database records:\n\n"
            f"{formatted_prompt}\n\n"
            "Organize the report into the required standard sections (Report Overview, Patient Information, "
            "Admission & Discharge Details, Recorded Diagnoses, Treatment and Procedures, Medication Information, "
            "Investigation and Test Results, Discharge Instructions, Follow-Up Information, Important Missing Information, "
            "and Overall Discharge Summary). Preserve all recorded values, dates, and units exactly. Do not invent any facts."
        )

        report_text = await self._generate_report(
            user_id=user_id,
            prompt=full_prompt,
            session_id=session_id,
            fallback_data=tool_result,
        )

        return DischargeSummaryResponse(
            package_id=clean_id,
            status="success",
            report=report_text,
            source_data=source_data,
            missing_information=missing_info,
            patient_id=patient_id,
            error=None,
        )

    async def _generate_report(
        self,
        user_id: str,
        prompt: str,
        session_id: Optional[str],
        fallback_data: Dict[str, Any],
    ) -> str:
        """Runs the Discharge Summary Report Agent with deterministic fallback."""
        try:
            active_session_id = await self.get_or_create_session(
                user_id=user_id,
                session_id=session_id,
            )

            content = types.Content(
                role="user",
                parts=[types.Part.from_text(text=prompt)],
            )

            agent_response = await collect_agent_text(
                lambda: self.runner.run_async(
                    user_id=user_id,
                    session_id=active_session_id,
                    new_message=content,
                )
            )
            if agent_response:
                return self._clean_report_text(agent_response)

        except Exception as e:
            logger.error("Discharge Summary Agent LLM generation failed: %s", str(e))

        # Deterministic rule-based fallback based strictly on verified database facts
        return self._generate_deterministic_report(fallback_data)

    @staticmethod
    def _clean_report_text(text: str) -> str:
        """Strips internal model reasoning or preamble tokens so output starts cleanly."""
        if not text:
            return ""
        markers = [
            "# Discharge Summary Report",
            "## 1. Report Overview",
            "### 1. Report Overview",
            "**1. Report Overview**",
            "# 1. Report Overview",
            "## Report Overview",
            "### Report Overview",
        ]
        for marker in markers:
            if marker in text:
                idx = text.find(marker)
                return text[idx:].strip()
        return text.strip()

    @staticmethod
    def _value(source: Dict[str, Any], key: str, fallback: str) -> str:
        """Reads a field, treating None and empty string as absent.

        The retrieval layer always sets these keys, so `dict.get(key, default)`
        never fires its default and a bare `None` ends up in the report.
        """
        value = source.get(key)
        if value is None or value == "":
            return fallback
        return str(value)

    @staticmethod
    def _generate_deterministic_report(data: Dict[str, Any]) -> str:
        """Deterministic, grounded report fallback if LLM service is temporarily unavailable."""
        pkg = data.get("package") or {}
        adm = data.get("admission") or {}
        pat = data.get("patient") or {}
        meds = data.get("medications") or []
        procs = data.get("procedures") or []
        labs = data.get("lab_results") or []
        missing = data.get("missing_information") or []

        pat_name = f"{pat.get('first_name', '')} {pat.get('last_name', '')}".strip() or "Unrecorded"

        report_lines = [
            f"# Discharge Summary Report — Package {pkg.get('package_id', 'N/A')}",
            "",
            "## 1. Report Overview",
            f"- **Package ID**: {DischargeSummaryService._value(pkg, 'package_id', 'N/A')}",
            f"- **Admission ID**: {DischargeSummaryService._value(pkg, 'admission_id', 'N/A')}",
            f"- **Package Status**: {DischargeSummaryService._value(pkg, 'package_status', 'N/A')}",
            f"- **Initiated At**: {DischargeSummaryService._value(pkg, 'initiated_at', 'N/A')}",
            f"- **Completed At**: {DischargeSummaryService._value(pkg, 'completed_at', 'Pending / Unrecorded')}",
            f"- **Multidisciplinary Reviews**: Physician: {DischargeSummaryService._value(pkg, 'physician_review_status', 'Pending')}, Pharmacist: {DischargeSummaryService._value(pkg, 'pharmacist_review_status', 'Pending')}, Nurse: {DischargeSummaryService._value(pkg, 'nurse_review_status', 'Pending')}",
            "",
            "## 2. Patient Information",
            f"- **Patient ID**: {DischargeSummaryService._value(pat, 'patient_id', 'N/A')}",
            f"- **Name**: {pat_name}",
            f"- **Gender**: {DischargeSummaryService._value(pat, 'gender', 'N/A')}",
            f"- **Blood Group**: {DischargeSummaryService._value(pat, 'blood_group', 'N/A')}",
            f"- **Known Allergies**: {DischargeSummaryService._value(pat, 'allergies', 'None documented')}",
            "",
            "## 3. Admission & Discharge Details",
            f"- **Admission Date**: {DischargeSummaryService._value(adm, 'admission_date', 'N/A')}",
            f"- **Discharge Date**: {DischargeSummaryService._value(adm, 'discharge_date', 'Pending / Ongoing')}",
            f"- **Admitting Diagnosis**: {DischargeSummaryService._value(adm, 'admitting_diagnosis', 'N/A')} (ICD-10: {DischargeSummaryService._value(adm, 'icd10_code', 'Not coded')})",
            f"- **Bed Number**: {DischargeSummaryService._value(adm, 'bed_number', 'N/A')}",
            f"- **Status**: {DischargeSummaryService._value(adm, 'admission_status', 'N/A')}",
            "",
            "## 4. Recorded Diagnoses",
            f"- **Primary Admitting Diagnosis**: {DischargeSummaryService._value(adm, 'admitting_diagnosis', 'None recorded')}",
            f"- **Clinical Summary**: {DischargeSummaryService._value(pkg, 'clinical_summary', 'None documented')}",
            "",
            "## 5. Treatment and Procedures",
        ]

        if procs:
            for p in procs:
                report_lines.append(f"- {p.get('procedure_name')} (Status: {p.get('procedure_status')})")
        else:
            report_lines.append("- No formal procedural interventions recorded.")

        report_lines.extend(["", "## 6. Medication Information"])
        if meds:
            for m in meds:
                report_lines.append(
                    f"- {m.get('medication_name')} ({m.get('drug_class', 'N/A')}): {m.get('strength')} via {m.get('route')} - {m.get('dose_instructions')} [{m.get('medication_status')}]"
                )
        else:
            report_lines.append("- No medication orders associated with this stay.")

        report_lines.extend(["", "## 7. Investigation and Test Results"])
        if labs:
            for l in labs:
                flag = f" [FLAG: {l.get('abnormal_flag')}]" if l.get("abnormal_flag") else ""
                report_lines.append(f"- {l.get('test_name')}: {l.get('result_value')} {l.get('result_unit')}{flag}")
        else:
            report_lines.append("- No diagnostic laboratory tests recorded.")

        report_lines.extend([
            "",
            "## 8. Discharge Instructions",
            f"- {DischargeSummaryService._value(pkg, 'patient_instructions', 'No specific instructions documented.')}",
            "",
            "## 9. Follow-Up Information",
            f"- {DischargeSummaryService._value(pkg, 'follow_up_plan', 'No follow-up plan documented.')}",
            "",
            "## 10. Important Missing Information",
        ])

        if missing:
            for item in missing:
                report_lines.append(f"- {item}")
        else:
            report_lines.append("- None identified.")

        report_lines.extend([
            "",
            "## 11. Overall Discharge Summary",
            f"Patient {pat_name} admitted for {DischargeSummaryService._value(adm, 'admitting_diagnosis', 'care')}. "
            f"Discharge package status is '{DischargeSummaryService._value(pkg, 'package_status', 'N/A')}'. "
            "Please review all follow-up plans and medication instructions with your attending clinician.",
        ])

        return "\n".join(report_lines)


# Default singleton instance
discharge_summary_service = DischargeSummaryService()

