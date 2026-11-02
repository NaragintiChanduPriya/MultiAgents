import logging
import uuid
from typing import Optional
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

from agents.appointment_agent import appointment_agent
from schemas.appointment_schema import AppointmentChatResponse
from services.llm_retry import (
    collect_agent_text,
    is_rate_limit_error,
    rate_limit_message,
)

logger = logging.getLogger(__name__)


class AppointmentService:
    """Coordinates execution of the independent Appointment Agent using Google ADK Runner."""

    def __init__(self, agent=None, session_service=None):
        self.app_name = "hospital_appointment_app"
        self.agent = agent or appointment_agent
        self.session_service = session_service or InMemorySessionService()
        self.runner = Runner(
            agent=self.agent,
            app_name=self.app_name,
            session_service=self.session_service,
        )

    async def get_or_create_session(
        self, patient_id: str, session_id: Optional[str] = None
    ) -> str:
        """Retrieves an existing session for the authenticated patient or creates a new one."""
        if session_id and session_id.strip():
            clean_session_id = session_id.strip()
            existing = await self.session_service.get_session(
                app_name=self.app_name,
                user_id=patient_id,
                session_id=clean_session_id,
            )
            if existing:
                return existing.id

        # Generate a new unique session identifier
        new_session_id = f"sess_{uuid.uuid4().hex[:16]}"
        session = await self.session_service.create_session(
            app_name=self.app_name,
            user_id=patient_id,
            session_id=new_session_id,
            state={"patient_id": patient_id},
        )
        return session.id

    async def process_chat(
        self,
        patient_id: str,
        message: str,
        session_id: Optional[str] = None,
    ) -> AppointmentChatResponse:
        """Processes a patient chat message through the independent Appointment Agent."""
        # 1. Validate patient identity
        if not patient_id or not patient_id.strip():
            return AppointmentChatResponse(
                session_id=session_id or "unknown",
                response="Authentication required. Missing patient identification.",
                status="error",
                error="Patient identity is required.",
            )

        clean_patient_id = patient_id.strip()
        clean_message = message.strip()

        # 2. Get or create session isolated by patient_id
        active_session_id = await self.get_or_create_session(
            patient_id=clean_patient_id,
            session_id=session_id,
        )

        # 3. Create ADK Content object
        user_content = types.Content(
            role="user",
            parts=[types.Part.from_text(text=clean_message)],
        )

        # 4. Invoke the agent via Runner.run_async, retrying through throttles
        try:
            final_response = await collect_agent_text(
                lambda: self.runner.run_async(
                    user_id=clean_patient_id,
                    session_id=active_session_id,
                    new_message=user_content,
                )
            )
            if not final_response:
                final_response = "I have processed your request. How else can I assist you with your appointment?"

            return AppointmentChatResponse(
                session_id=active_session_id,
                response=final_response,
                status="success",
            )

        except Exception as e:
            logger.error("Agent execution failed for session %s: %s", active_session_id, str(e))

            # A throttle is temporary and the patient can act on it, so say so
            # rather than reporting it as a generic failure.
            if is_rate_limit_error(e):
                return AppointmentChatResponse(
                    session_id=active_session_id,
                    response=rate_limit_message(e, "appointment assistant"),
                    status="rate_limited",
                    error="Provider rate limit reached.",
                )

            return AppointmentChatResponse(
                session_id=active_session_id,
                response="I encountered an error processing your appointment request. Please try again.",
                status="error",
                error=f"Execution error: {str(e)}",
            )


# Default service instance for application routing
appointment_service = AppointmentService()

