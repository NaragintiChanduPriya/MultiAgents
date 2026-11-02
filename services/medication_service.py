import logging
from typing import Optional
import uuid

from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

from agents.medication_agent import medication_agent
from agents.tools.medication_tools import (
    format_medication_record_for_prompt,
    get_medication_by_record_id,
)
from agents.tools.prescription_tools import (
    format_prescription_for_prompt,
    get_prescription_by_id,
)
from schemas.medication_schema import (
    MedicationDetailResponse,
    MedicationRecordItem,
    PrescriptionCounsellingResponse,
)
from services.llm_retry import (
    collect_agent_text,
    is_rate_limit_error,
    rate_limit_message,
)

logger = logging.getLogger(__name__)


class MedicationService:
    """Coordinates execution of the independent Medication Agent using Google ADK Runner.
    
    MongoDB is strictly the source of truth for all medication data.
    The LLM is invoked solely to produce simple, patient-friendly explanations
    of verified database records.
    """

    def __init__(self, agent=None, session_service=None):
        self.app_name = "hospital_medication_app"
        self.agent = agent or medication_agent
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

        new_session_id = f"med_sess_{uuid.uuid4().hex[:16]}"
        session = await self.session_service.create_session(
            app_name=self.app_name,
            user_id=user_id,
            session_id=new_session_id,
            state={"user_id": user_id},
        )
        return session.id

    async def get_medication_details(
        self,
        medication_record_id: str,
        authenticated_patient_id: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> MedicationDetailResponse:
        """Retrieves and explains a medication record using verified database data.
        
        Args:
            medication_record_id: Unique record identifier to look up.
            authenticated_patient_id: Verified caller patient ID for authorization check.
            session_id: Optional ADK conversation session ID.
            
        Returns:
            MedicationDetailResponse with structured record and human explanation.
        """
        # 1. Validate identifier
        if not medication_record_id or not medication_record_id.strip():
            return MedicationDetailResponse(
                medication_record_id=medication_record_id or "",
                status="error",
                error="Medication record identifier is required and cannot be empty.",
                explanation="Please provide a valid medication record identifier.",
                record=None,
            )

        clean_id = medication_record_id.strip()

        # 2. Query MongoDB as the absolute source of truth
        tool_result = get_medication_by_record_id(clean_id)

        # 3. Handle database connection or execution errors
        if tool_result.get("status") == "error":
            return MedicationDetailResponse(
                medication_record_id=clean_id,
                status="error",
                error=tool_result.get("message", "Database error occurred."),
                explanation="We are currently unable to retrieve medication records due to a database issue. Please try again later.",
                record=None,
            )

        # 4. Handle non-existent record (no matching document)
        if not tool_result.get("found") or not tool_result.get("record"):
            return MedicationDetailResponse(
                medication_record_id=clean_id,
                status="not_found",
                error=f"Medication record '{clean_id}' was not found in hospital database records.",
                explanation=f"No medication record matches the supplied identifier '{clean_id}'. Please check the record identifier and try again.",
                record=None,
            )

        raw_record = tool_result["record"]

        # 5. Authorization check: if authenticated user is a patient, verify ownership
        record_patient_id = raw_record.get("patient_id")
        if authenticated_patient_id and authenticated_patient_id.strip():
            caller_id = authenticated_patient_id.strip()
            # If caller is identified as a patient (e.g. PAT...), verify match
            if caller_id.startswith("PAT") and record_patient_id != caller_id:
                logger.warning(
                    "Unauthorized access attempt: Patient %s tried to access medication %s belonging to %s",
                    caller_id,
                    clean_id,
                    record_patient_id,
                )
                return MedicationDetailResponse(
                    medication_record_id=clean_id,
                    status="forbidden",
                    error="Access denied. You are not authorized to view medication records for another patient.",
                    explanation="",
                    patient_id=None,
                    record=None,
                )

        # 6. Parse structured record
        structured_item = MedicationRecordItem(**raw_record)

        # 7. Generate patient-friendly explanation via Medication Agent
        user_id = authenticated_patient_id or record_patient_id or "patient_guest"
        formatted_text = format_medication_record_for_prompt(raw_record)

        prompt_message = (
            f"Here is the verified hospital medication record for record ID '{clean_id}':\n\n"
            f"{formatted_text}\n\n"
            "Please provide a simple, readable explanation of this medication record. "
            "Explain what the drug is, its recorded strength, route, and instructions in clear patient-friendly language. "
            "Include the standard hospital educational notice and preserve all recorded values exactly."
        )

        explanation = await self._generate_explanation(
            user_id=user_id,
            prompt=prompt_message,
            session_id=session_id,
            fallback_record=raw_record,
        )

        return MedicationDetailResponse(
            medication_record_id=clean_id,
            status="success",
            record=structured_item,
            explanation=explanation,
            patient_id=record_patient_id,
            error=None,
        )

    async def _generate_explanation(
        self,
        user_id: str,
        prompt: str,
        session_id: Optional[str],
        fallback_record: dict,
    ) -> str:
        """Runs the Medication Agent to explain the record, with a deterministic fallback."""
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
                return self._clean_explanation_text(agent_response)

        except Exception as e:
            logger.error("Medication Agent LLM explanation failed: %s", str(e))

        # Deterministic rule-based fallback based strictly on verified database facts
        return self._generate_deterministic_explanation(fallback_record)

    async def generate_counselling(
        self,
        prescription_id: str,
        authenticated_patient_id: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> PrescriptionCounsellingResponse:
        """Writes the take-home sheet for a prescription.

        The prescription is read from the database first and the agent is given
        only those verified facts. It is asked to phrase them, never to decide
        them: the drugs, doses and durations are the prescriber's, and the
        safety flags are the rules engine's.

        Args:
            prescription_id: The prescription to counsel on.
            authenticated_patient_id: Verified caller, for the ownership check.
            session_id: Optional conversation session identifier.
        """
        clean_id = (prescription_id or "").strip()
        if not clean_id:
            return PrescriptionCounsellingResponse(
                prescription_id=prescription_id or "",
                status="error",
                error="A prescription identifier is required.",
            )

        # 1. The database is the source of truth. Read before writing anything.
        data = get_prescription_by_id(clean_id)
        if data["status"] == "not_found":
            return PrescriptionCounsellingResponse(
                prescription_id=clean_id,
                status="not_found",
                error=data.get("message") or f"Prescription '{clean_id}' not found.",
            )
        if data["status"] == "error":
            return PrescriptionCounsellingResponse(
                prescription_id=clean_id,
                status="error",
                error=data.get("message") or "Could not read the prescription.",
            )

        patient = data.get("patient") or {}
        owner_id = patient.get("patient_id")

        # 2. A patient may only be counselled on their own prescription. Staff
        #    callers carry no PAT identity and are not checked here — the API
        #    layer has already decided they may see this record.
        caller = (authenticated_patient_id or "").strip()
        if caller.upper().startswith("PAT") and owner_id and caller != owner_id:
            logger.warning(
                "Caller %s attempted counselling for a prescription owned by %s",
                caller,
                owner_id,
            )
            return PrescriptionCounsellingResponse(
                prescription_id=clean_id,
                status="forbidden",
                error="You are not authorised to view this prescription.",
            )

        if not data.get("items"):
            return PrescriptionCounsellingResponse(
                prescription_id=clean_id,
                status="error",
                error="This prescription has no medicines on it.",
            )

        verified = format_prescription_for_prompt(data)
        reading_level = patient.get("literacy_level", "intermediate")

        prompt = (
            "Write the counselling sheet a patient takes home with their medicines, "
            "using ONLY the verified prescription below.\n\n"
            f"{verified}\n\n"
            "Rules you must follow:\n"
            "- Use only the medicines, doses, frequencies and durations listed above. "
            "Never add a medicine, change a dose, or invent a duration.\n"
            f"- Write for a {reading_level} reading level, in short plain sentences. "
            "Address the patient directly as 'you'.\n"
            "- Cover, for each medicine: what it is for in everyday words, exactly how and "
            "when to take it, and how long to continue.\n"
            "- Add a short section on what to do about a missed dose, and one on side "
            "effects or warning signs that mean they should contact the hospital.\n"
            "- If the pharmacy supplied a substitute, say so plainly so the patient is not "
            "confused by a different name on the box.\n"
            "- Do NOT diagnose, do not advise beyond these medicines, and do not suggest "
            "stopping or changing anything. Tell them to speak to their doctor or "
            "pharmacist instead.\n"
            "- End with: 'Questions about your medicines? Call the hospital pharmacy on "
            "+91 44 4000 1200.'\n"
            "- Begin immediately with the sheet itself. No preamble, no internal reasoning."
        )

        user_id = authenticated_patient_id or owner_id or "pharmacy_counter"

        try:
            active_session_id = await self.get_or_create_session(
                user_id=user_id, session_id=session_id
            )
            content = types.Content(role="user", parts=[types.Part.from_text(text=prompt)])
            text = await collect_agent_text(
                lambda: self.runner.run_async(
                    user_id=user_id,
                    session_id=active_session_id,
                    new_message=content,
                )
            )
            if text:
                return PrescriptionCounsellingResponse(
                    prescription_id=clean_id,
                    status="success",
                    counselling=text.strip(),
                )
        except Exception as e:
            logger.error("Counselling generation failed for %s: %s", clean_id, str(e))
            if is_rate_limit_error(e):
                return PrescriptionCounsellingResponse(
                    prescription_id=clean_id,
                    status="error",
                    error=rate_limit_message(e, "medication assistant"),
                )

        # 3. The patient still leaves with something usable when the model is
        #    unavailable. A deterministic sheet beats an empty one.
        return PrescriptionCounsellingResponse(
            prescription_id=clean_id,
            status="success",
            counselling=self._deterministic_counselling(data),
        )

    @staticmethod
    def _deterministic_counselling(data: dict) -> str:
        """A plain sheet built straight from the record, with no model involved."""
        items = data.get("items") or []
        lines = [
            "# Your medicines",
            "",
            "Below is exactly what the doctor prescribed and the pharmacy has given you.",
            "",
        ]
        for index, item in enumerate(items, start=1):
            lines.append(f"## {index}. {item.get('medication_name', 'Medicine')}")
            if item.get("drug_class"):
                lines.append(f"- Type of medicine: {item['drug_class']}")
            lines.append(f"- Dose: {item.get('strength') or 'As written on the label'}")
            lines.append(f"- How often: {item.get('frequency') or 'As written on the label'}")
            lines.append(f"- How to take it: {item.get('route') or 'As directed'}")
            lines.append(
                f"- How long: {item.get('duration_text', 'Until your doctor reviews it')}"
            )
            if item.get("instructions"):
                lines.append(f"- Your doctor's instructions: {item['instructions']}")
            if item.get("substituted_with"):
                lines.append(
                    f"- The pharmacy supplied **{item['substituted_with']}**. This is the "
                    "same medicine under a different name."
                )
            lines.append("")

        lines += [
            "## If you miss a dose",
            "Take it as soon as you remember, unless it is nearly time for the next one. "
            "Never take two doses together to catch up.",
            "",
            "## When to get help",
            "Contact the hospital if you have a rash, swelling, difficulty breathing, "
            "unusual bleeding or bruising, or anything else that worries you after taking "
            "these medicines.",
            "",
            "Do not stop, change or share these medicines without speaking to your doctor "
            "or pharmacist first.",
            "",
            "Questions about your medicines? Call the hospital pharmacy on +91 44 4000 1200.",
        ]
        return "\n".join(lines)

    @staticmethod
    def _clean_explanation_text(text: str) -> str:
        """Strips internal model reasoning or preamble tokens so output starts cleanly."""
        if not text:
            return ""
        markers = [
            "**1. Medication Record Summary**",
            "### 1. Medication Record Summary",
            "## 1. Medication Record Summary",
            "# 1. Medication Record Summary",
            "**Medication Record Summary**",
            "### Medication Record Summary",
        ]
        for marker in markers:
            if marker in text:
                idx = text.find(marker)
                return text[idx:].strip()
        return text.strip()

    @staticmethod
    def _generate_deterministic_explanation(record: dict) -> str:
        """Deterministic, grounded explanation fallback if the LLM service is temporarily unavailable."""
        name = record.get("medication_name", "Unknown")
        drug_class = record.get("drug_class", "Therapeutic medication")
        strength = record.get("strength", "as documented")
        route = record.get("route", "unspecified route")
        instructions = record.get("dose_instructions", "as directed by your clinician")
        status_val = record.get("medication_status", "documented")
        med_type = record.get("medication_type", "order")

        return (
            f"### Medication Record: {name}\n\n"
            f"- **Classification**: {drug_class}\n"
            f"- **Strength**: {strength}\n"
            f"- **Route**: Taken via {route.lower()}\n"
            f"- **Instructions**: {instructions}\n"
            f"- **Type & Status**: {med_type} medication currently recorded as '{status_val}'.\n\n"
            "*Notice: This is an educational summary of your hospital record. Do not alter or stop any medication without consulting your doctor or pharmacist.*"
        )


# Default singleton service instance
medication_service = MedicationService()
