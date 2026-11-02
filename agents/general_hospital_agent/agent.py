"""ADK standard discovery entrypoint for general_hospital_agent."""

from agents.general_hospital_agent.general_hospital_agent import (
    create_general_hospital_agent,
    general_hospital_agent,
    root_agent,
)

__all__ = [
    "root_agent",
    "general_hospital_agent",
    "create_general_hospital_agent",
]

