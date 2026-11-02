import os
from typing import Optional
from google.adk.agents.llm_agent import Agent
from google.adk.models.lite_llm import LiteLlm

from agents.groq_compat import apply_groq_compatibility_patches
from agents.identity_guard import bind_verified_identity

# Keep Groq's chain-of-thought out of the answer text and out of replayed history.
apply_groq_compatibility_patches()

from config import settings
from agents.discharge_summary_report_agent.instruction import (
    DISCHARGE_SUMMARY_REPORT_INSTRUCTION,
)
from agents.tools.discharge_summary_tools import (
    get_discharge_package_by_id,
    list_discharge_packages,
)


def create_discharge_summary_report_agent(model_name: Optional[str] = None) -> Agent:
    """Factory creating an independent Hospital Discharge Summary Report Agent.
    
    No parent agent, no orchestrator, no sub-agents.
    """
    # Ensure Groq API key is populated in environment for LiteLLM
    if settings.groq_api_key:
        os.environ["GROQ_API_KEY"] = settings.groq_api_key

    selected_model_str = model_name or settings.adk_groq_model
    llm = LiteLlm(model=selected_model_str)

    tools = [
        get_discharge_package_by_id,
        list_discharge_packages,
    ]

    agent = Agent(
        name="discharge_summary_report_agent",
        description="Independent Hospital Discharge Summary Report Agent for compiling verified discharge packages.",
        instruction=DISCHARGE_SUMMARY_REPORT_INSTRUCTION,
        model=llm,
        tools=tools,
        # The caller's identity is enforced here, not requested in the prompt:
        # every identity argument is overwritten with the verified caller
        # before the tool runs.
        before_tool_callback=bind_verified_identity,
    )
    return agent


# Default singleton instance for application use
discharge_summary_report_agent = create_discharge_summary_report_agent()

# Standard Google ADK Web UI discovery variable
root_agent = discharge_summary_report_agent

