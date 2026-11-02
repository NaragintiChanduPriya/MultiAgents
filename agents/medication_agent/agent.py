"""ADK standard discovery entrypoint for medication_agent."""

import sys
from pathlib import Path

# Ensure project root is on sys.path regardless of working directory
_project_root = str(Path(__file__).resolve().parent.parent.parent)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from agents.medication_agent.medication_agent import (
    create_medication_agent,
    medication_agent,
    root_agent,
)

__all__ = ["root_agent", "medication_agent", "create_medication_agent"]

