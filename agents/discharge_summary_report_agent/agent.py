"""ADK standard discovery entrypoint for discharge_summary_report_agent."""

import sys
from pathlib import Path

# Ensure project root is on sys.path regardless of working directory
_project_root = str(Path(__file__).resolve().parent.parent.parent)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from agents.discharge_summary_report_agent.discharge_summary_report_agent import (
    create_discharge_summary_report_agent,
    discharge_summary_report_agent,
    root_agent,
)

__all__ = [
    "root_agent",
    "discharge_summary_report_agent",
    "create_discharge_summary_report_agent",
]

