from routes.appointment_routes import router as appointment_router
from routes.discharge_summary_routes import router as discharge_summary_router
from routes.general_hospital_routes import router as general_hospital_router
from routes.medication_routes import router as medication_router

__all__ = [
    "appointment_router",
    "medication_router",
    "discharge_summary_router",
    "general_hospital_router",
]
