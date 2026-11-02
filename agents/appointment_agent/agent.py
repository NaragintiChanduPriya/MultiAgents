"""ADK standard discovery entrypoint for appointment_agent."""

import sys
from pathlib import Path

# Ensure project root is on sys.path regardless of working directory
_project_root = str(Path(__file__).resolve().parent.parent.parent)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from agents.appointment_agent.appointment_agent import (
    appointment_agent,
    create_appointment_agent,
    root_agent,
)

__all__ = ["root_agent", "appointment_agent", "create_appointment_agent"]


