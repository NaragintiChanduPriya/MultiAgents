import logging
from fastapi import FastAPI, status
from fastapi.responses import JSONResponse

from config import settings
from database import check_db_health
from routes.appointment_routes import router as appointment_router
from routes.discharge_summary_routes import router as discharge_summary_router
from routes.general_hospital_routes import router as general_hospital_router
from routes.medication_routes import router as medication_router
from schemas.appointment_schema import HealthResponse

# Configure structured logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("hospital_main")

app = FastAPI(
    title=settings.app_name,
    version="1.0.0",
    description="Hospital Multi-Agent System — Independent Specialist Agents (Appointment, Medication, Discharge Summary, General Hospital Assistant)",
)

# Register independent agent routers
app.include_router(appointment_router)
app.include_router(medication_router)
app.include_router(discharge_summary_router)
app.include_router(general_hospital_router)


@app.get(
    "/api/v1/health",
    response_model=HealthResponse,
    status_code=status.HTTP_200_OK,
    summary="Health check endpoint",
    description="Verifies application status and MongoDB database connectivity.",
)
async def health_check() -> HealthResponse:
    """Verifies service health and tests live database connectivity."""
    db_connected, db_error = check_db_health()
    overall_status = "healthy" if db_connected else "degraded"

    return HealthResponse(
        status=overall_status,
        app_name=settings.app_name,
        database_connected=db_connected,
        database_error=db_error,
    )


@app.exception_handler(Exception)
async def global_exception_handler(request, exc):
    """Sanitizes unexpected server errors to prevent exposing internal stack traces or credentials."""
    logger.error("Unhandled error processing request %s: %s", request.url.path, str(exc))
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"detail": "An internal server error occurred. Please contact hospital support."},
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
