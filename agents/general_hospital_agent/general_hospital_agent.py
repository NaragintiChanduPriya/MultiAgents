import os
from typing import Optional
from google.adk.agents.llm_agent import Agent
from google.adk.models.lite_llm import LiteLlm

from agents.groq_compat import apply_groq_compatibility_patches

# Keep Groq's chain-of-thought out of the answer text and out of replayed history.
apply_groq_compatibility_patches()

from config import settings
from agents.general_hospital_agent.instruction import (
    GENERAL_HOSPITAL_AGENT_INSTRUCTION,
)
from agents.tools.general_hospital_tools import (
    get_hospital_information,
    search_hospital_departments,
    search_hospital_policies,
    search_hospital_services,
)


def create_general_hospital_agent(model_name: Optional[str] = None) -> Agent:
    """Factory creating an independent General Hospital Assistant Agent.
    
    No parent agent, no orchestrator, no sub-agents.
    """
    if settings.groq_api_key:
        os.environ["GROQ_API_KEY"] = settings.groq_api_key

    selected_model_str = model_name or settings.adk_groq_model
    llm = LiteLlm(model=selected_model_str)

    tools = [
        search_hospital_departments,
        search_hospital_services,
        get_hospital_information,
        search_hospital_policies,
    ]

    agent = Agent(
        name="general_hospital_agent",
        description="Independent General Hospital Assistant Agent for answering non-specialized hospital inquiries.",
        instruction=GENERAL_HOSPITAL_AGENT_INSTRUCTION,
        model=llm,
        tools=tools,
    )
    return agent


# Default singleton instance
general_hospital_agent = create_general_hospital_agent()

# Standard Google ADK Web UI discovery variable
root_agent = general_hospital_agent

