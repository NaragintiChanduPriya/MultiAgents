import os
from typing import Optional
from google.adk.agents.llm_agent import Agent
from google.adk.models.lite_llm import LiteLlm

from agents.groq_compat import apply_groq_compatibility_patches
from agents.identity_guard import bind_verified_identity

# Keep Groq's chain-of-thought out of the answer text and out of replayed history.
apply_groq_compatibility_patches()

from config import settings
from agents.appointment_agent.instruction import APPOINTMENT_AGENT_INSTRUCTION
from agents.tools.inspect_schema import inspect_database_schema
from agents.tools.query_data import (
    check_slot_availability,
    get_appointment_details,
    get_available_doctor_slots,
    get_doctor_details,
    get_doctors_by_specialty,
    get_patient_appointments,
    list_specialties,
)
from agents.tools.write_data import cancel_appointment, create_appointment


def create_appointment_agent(model_name: Optional[str] = None) -> Agent:
    """Factory creating an independent Hospital Appointment Agent.
    
    No parent agent, no orchestrator, no sub-agents.
    """
    # Ensure Groq API key is populated in environment for LiteLLM
    if settings.groq_api_key:
        os.environ["GROQ_API_KEY"] = settings.groq_api_key

    selected_model_str = model_name or settings.adk_groq_model
    llm = LiteLlm(model=selected_model_str)

    tools = [
        inspect_database_schema,
        list_specialties,
        get_doctors_by_specialty,
        get_doctor_details,
        get_available_doctor_slots,
        check_slot_availability,
        get_appointment_details,
        get_patient_appointments,
        create_appointment,
        cancel_appointment,
    ]

    agent = Agent(
        name="appointment_agent",
        description="Independent Hospital Appointment Agent for doctor inquiries and scheduling.",
        instruction=APPOINTMENT_AGENT_INSTRUCTION,
        model=llm,
        tools=tools,
        # The caller's identity is enforced here, not requested in the prompt:
        # every identity argument is overwritten with the verified caller
        # before the tool runs.
        before_tool_callback=bind_verified_identity,
    )
    return agent


# Default singleton instance for application use
appointment_agent = create_appointment_agent()

# Standard Google ADK Web UI discovery variable
root_agent = appointment_agent
