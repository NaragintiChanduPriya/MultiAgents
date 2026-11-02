import logging
from typing import List, Optional, Tuple
import uuid

from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

from agents.general_hospital_agent import general_hospital_agent
from agents.tools.general_hospital_tools import (
    get_hospital_information,
    search_hospital_departments,
    search_hospital_policies,
    search_hospital_services,
)
from schemas.general_hospital_schema import GeneralHospitalResponse
from services.llm_retry import collect_agent_text

logger = logging.getLogger(__name__)

# Keywords indicating acute medical emergencies requiring immediate triage
EMERGENCY_KEYWORDS = [
    "chest pain",
    "heart attack",
    "shortness of breath",
    "cannot breathe",
    "can't breathe",
    "severe bleeding",
    "bleeding heavily",
    "stroke",
    "face drooping",
    "slurred speech",
    "unconscious",
    "passed out",
    "collapsed",
    "overdose",
    "anaphylaxis",
    "seizure",
]


class GeneralHospitalService:
    """Coordinates execution of the independent General Hospital Assistant Agent using Google ADK Runner."""

    def __init__(self, agent=None, session_service=None):
        self.app_name = "hospital_general_assistant_app"
        self.agent = agent or general_hospital_agent
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

        new_session_id = f"gen_sess_{uuid.uuid4().hex[:16]}"
        session = await self.session_service.create_session(
            app_name=self.app_name,
            user_id=user_id,
            session_id=new_session_id,
            state={"user_id": user_id},
        )
        return session.id

    def check_emergency_triage(self, question: str) -> Optional[str]:
        """Screens for acute life-threatening emergencies requiring immediate dispatch."""
        q_lower = question.lower()
        for kw in EMERGENCY_KEYWORDS:
            if kw in q_lower:
                return (
                    "EMERGENCY ADVISORY: If you or someone with you is experiencing a medical emergency "
                    f"(such as {kw}), please immediately call emergency medical services on 108 "
                    "or proceed directly to the nearest hospital Emergency Room / Casualty Department without delay. "
                    "Do not wait for an online response or attempt routine appointment scheduling for acute life-threatening symptoms."
                )
        return None

    def gather_verified_context(self, question: str) -> Tuple[str, List[str]]:
        """Gathers verified context from MongoDB and institutional documentation matching the question."""
        q_lower = question.lower()
        context_parts = []
        sources = []

        # 1. Department inquiries
        if any(w in q_lower for w in ["department", "specialt", "cardio", "neuro", "ortho", "pulmon", "doctor"]):
            dept_res = search_hospital_departments()
            if dept_res.get("departments"):
                depts = dept_res["departments"]
                dept_lines = ["Verified Hospital Departments (from MongoDB):"]
                for d in depts:
                    details = [f"Active Doctors: {d.get('doctor_count', 0)}"]
                    if d.get("tagline"):
                        details.append(d["tagline"])
                    if d.get("common_conditions"):
                        details.append("Treats: " + ", ".join(d["common_conditions"]))
                    if d.get("location"):
                        details.append(f"Located: {d['location']}")
                    if d.get("consultation_fee_inr"):
                        details.append(f"Consultation fee: INR {d['consultation_fee_inr']}")
                    dept_lines.append(f"- {d['department']}: " + " | ".join(details))
                context_parts.append("\n".join(dept_lines))
                sources.append("MongoDB: specialties and users collections")

        # 2. Services inquiries
        if any(w in q_lower for w in ["service", "facility", "care", "lab", "test", "pharmacy", "physio"]):
            serv_res = search_hospital_services()
            if serv_res.get("services"):
                serv_lines = ["Verified Hospital Services:"]
                for s in serv_res["services"]:
                    serv_lines.append(f"- {s['service_name']} ({s['category']}): {s['description']}")
                context_parts.append("\n".join(serv_lines))
                sources.append("Hospital Operational Services Directory")

        # 3. Policy inquiries (registration, admission, visiting hours, documents)
        if any(w in q_lower for w in ["register", "new patient", "document", "bring", "admission", "admit", "visit", "hour", "rule", "policy"]):
            policy_res = search_hospital_policies()
            if policy_res.get("policies"):
                pol_lines = ["Verified Hospital Policies & Guidelines:"]
                for p in policy_res["policies"]:
                    pol_lines.append(f"### {p['title']}")
                    if p.get("requirements"):
                        pol_lines.append("Requirements: " + "; ".join(p["requirements"]))
                    if p.get("procedure"):
                        pol_lines.append("Procedure: " + p["procedure"])
                    if p.get("visiting_hours"):
                        pol_lines.append("Hours: " + "; ".join(p["visiting_hours"]))
                    if p.get("guidelines"):
                        pol_lines.append("Guidelines: " + "; ".join(p["guidelines"]))
                context_parts.append("\n".join(pol_lines))
                sources.append("Hospital Institutional Policies")

        # 4. Operational / contact information
        if any(w in q_lower for w in ["contact", "hour", "time", "where", "location", "address", "navigate", "help"]):
            info_res = get_hospital_information()
            if info_res.get("information"):
                info_lines = ["Verified Hospital Operating & Facility Information:"]
                for k, v in info_res["information"].items():
                    val_str = ", ".join(v) if isinstance(v, list) else str(v)
                    info_lines.append(f"- {k.replace('_', ' ').title()}: {val_str}")
                context_parts.append("\n".join(info_lines))
                sources.append("Hospital Operational Directory")

        if not sources:
            sources.append("Hospital General Knowledge Base")

        return "\n\n".join(context_parts), sources

    async def answer_question(
        self,
        question: str,
        user_id: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> GeneralHospitalResponse:
        """Processes a general hospital inquiry with ground truth verification and emergency screening."""
        clean_question = question.strip()
        active_user_id = user_id.strip() if user_id and user_id.strip() else "guest_visitor"

        # 1. Emergency screening
        emergency_alert = self.check_emergency_triage(clean_question)
        if emergency_alert:
            return GeneralHospitalResponse(
                question=clean_question,
                status="emergency_alert",
                answer=emergency_alert,
                sources=["Hospital Emergency Triage Protocol"],
                session_id=session_id,
            )

        # 2. Gather verified context
        verified_context, sources = self.gather_verified_context(clean_question)

        prompt_message = (
            f"User Question: {clean_question}\n\n"
            f"Relevant Verified Hospital Information:\n{verified_context if verified_context else 'None found in current database records.'}\n\n"
            "Please provide a helpful, clear, and professional response directly to the user. "
            "Do not output internal preambles or thoughts; begin immediately with your answer. "
            "Use verified facts where provided, distinguish verified facts from general guidance, "
            "and clearly indicate if specific details cannot be verified. "
            "If the user is asking about booking appointments, detailed medical records, medication orders, or discharge packages, "
            "politely direct them to the appropriate hospital agent/endpoint."
        )

        # 3. Invoke ADK Agent
        try:
            active_session_id = await self.get_or_create_session(
                user_id=active_user_id,
                session_id=session_id,
            )

            content = types.Content(
                role="user",
                parts=[types.Part.from_text(text=prompt_message)],
            )

            agent_response = await collect_agent_text(
                lambda: self.runner.run_async(
                    user_id=active_user_id,
                    session_id=active_session_id,
                    new_message=content,
                )
            )
            if agent_response:
                return GeneralHospitalResponse(
                    question=clean_question,
                    status="success",
                    answer=self._clean_response_text(agent_response),
                    sources=sources,
                    session_id=active_session_id,
                )

        except Exception as e:
            logger.error("General Hospital Agent LLM invocation failed: %s", str(e))

        # 4. Deterministic fallback if LLM is unavailable
        fallback_answer = self._generate_deterministic_fallback(clean_question, verified_context)
        return GeneralHospitalResponse(
            question=clean_question,
            status="success",
            answer=fallback_answer,
            sources=sources,
            session_id=session_id,
        )

    @staticmethod
    def _clean_response_text(text: str) -> str:
        """Strips internal thought traces or reasoning preambles if present."""
        if not text:
            return ""
        import re
        cleaned = re.sub(
            r"^(?:The user (?:asks|is asking)|We need to answer).*?(?:Just respond\.|So respond\.|Let's respond\.)\s*",
            "",
            text,
            flags=re.DOTALL | re.IGNORECASE,
        )
        return cleaned.strip()

    @staticmethod
    def _generate_deterministic_fallback(question: str, context: str) -> str:
        """Deterministic response if LLM is temporarily unreachable."""
        if context:
            return (
                "Based on verified hospital records, here is the relevant information regarding your inquiry:\n\n"
                f"{context}\n\n"
                "If you need further assistance or wish to schedule an appointment with a specialist, "
                "please contact the hospital central helpdesk at the main entrance or use the Appointment Agent."
            )
        return (
            "We could not verify specific hospital records matching your request at this moment. "
            "Please contact the Hospital Helpdesk at the main ground floor entrance for in-person assistance."
        )


# Default singleton instance
general_hospital_service = GeneralHospitalService()
