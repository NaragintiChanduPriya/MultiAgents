from services.appointment_service import AppointmentService, appointment_service
from services.discharge_summary_service import (
    DischargeSummaryService,
    discharge_summary_service,
)
from services.general_hospital_service import (
    GeneralHospitalService,
    general_hospital_service,
)
from services.medication_service import MedicationService, medication_service

__all__ = [
    "AppointmentService",
    "appointment_service",
    "MedicationService",
    "medication_service",
    "DischargeSummaryService",
    "discharge_summary_service",
    "GeneralHospitalService",
    "general_hospital_service",
]
