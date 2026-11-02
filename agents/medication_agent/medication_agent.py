import os
from typing import Optional
from google.adk.agents.llm_agent import Agent
from google.adk.models.lite_llm import LiteLlm

from agents.groq_compat import apply_groq_compatibility_patches
from agents.identity_guard import bind_verified_identity

# Keep Groq's chain-of-thought out of the answer text and out of replayed history.
apply_groq_compatibility_patches()

from config import settings
from agents.medication_agent.instruction import MEDICATION_AGENT_INSTRUCTION
from agents.tools.medication_tools import (
    get_medication_by_record_id,
    list_medications_for_admission,
    list_medications_for_patient,
)
from agents.tools.prescription_tools import (
    get_prescription_by_id,
    list_prescriptions_for_patient,
)


def create_medication_agent(model_name: Optional[str] = None) -> Agent:
    """Factory creating an independent Hospital Medication Agent.
    
    No parent agent, no orchestrator, no sub-agents.
    """
    # Ensure Groq API key is populated in environment for LiteLLM
    if settings.groq_api_key:
        os.environ["GROQ_API_KEY"] = settings.groq_api_key

    selected_model_str = model_name or settings.adk_groq_model
    llm = LiteLlm(model=selected_model_str)

    tools = [
        get_medication_by_record_id,
        list_medications_for_patient,
        list_medications_for_admission,
        get_prescription_by_id,
        list_prescriptions_for_patient,
    ]

    agent = Agent(
        name="medication_agent",
        description="Independent Hospital Medication Agent for retrieving and explaining patient medication records.",
        instruction=MEDICATION_AGENT_INSTRUCTION,
        model=llm,
        tools=tools,
        # The caller's identity is enforced here, not requested in the prompt:
        # every identity argument is overwritten with the verified caller
        # before the tool runs.
        before_tool_callback=bind_verified_identity,
    )
    return agent


# Default singleton instance for application use
medication_agent = create_medication_agent()

# Standard Google ADK Web UI discovery variable
root_agent = medication_agent

