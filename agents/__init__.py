from agents.appointment_agent import appointment_agent
from agents.discharge_summary_report_agent import discharge_summary_report_agent
from agents.general_hospital_agent import general_hospital_agent
from agents.medication_agent import medication_agent

root_agent = appointment_agent

__all__ = [
    "appointment_agent",
    "discharge_summary_report_agent",
    "general_hospital_agent",
    "medication_agent",
    "root_agent",
]
