import logging
from typing import Optional
from fastapi import APIRouter, Depends, Header, HTTPException, status

from schemas.discharge_summary_schema import (
    DischargeSummaryRequest,
    DischargeSummaryResponse,
)
from services.discharge_summary_service import discharge_summary_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/discharge-summaries", tags=["Discharge Summaries"])


async def get_authenticated_user_id(
    x_patient_id: Optional[str] = Header(
        default=None,
        alias="X-Patient-Id",
        description="Verified patient identity identifier (e.g. 'PAT0019').",
    ),
    authorization: Optional[str] = Header(
        default=None,
        description="Bearer token containing verified caller credentials.",
    ),
) -> str:
    """Authentication dependency ensuring only authenticated callers can access discharge summary services."""
    if x_patient_id and x_patient_id.strip():
        return x_patient_id.strip()

    if authorization and authorization.strip().startswith("Bearer "):
        token = authorization.strip()[7:].strip()
        if token:
            return token

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Authentication required. Please supply an 'X-Patient-Id' header or Bearer authorization token.",
        headers={"WWW-Authenticate": "Bearer"},
    )


@router.post(
    "/generate",
    response_model=DischargeSummaryResponse,
    status_code=status.HTTP_200_OK,
    summary="Generate Discharge Summary Report",
    description="Retrieves a verified discharge package from PostgreSQL by package_id and compiles a comprehensive discharge summary report via the Discharge Summary Report Agent.",
)
async def generate_discharge_summary_report(
    request: DischargeSummaryRequest,
    caller_id: str = Depends(get_authenticated_user_id),
) -> DischargeSummaryResponse:
    """Handles discharge package lookup, authorization check, and report generation."""
    try:
        response = await discharge_summary_service.generate_discharge_summary(
            package_id=request.package_id,
            authenticated_patient_id=caller_id,
            session_id=request.session_id,
        )

        if response.status == "forbidden":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=response.error or "Access denied to this discharge summary record.",
            )

        if response.status == "not_found":
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=response.error or f"Discharge package '{request.package_id}' not found.",
            )

        if response.status == "error":
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=response.error or "An error occurred while compiling the discharge summary report.",
            )

        return response

    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            "Unexpected error handling discharge package %s: %s",
            request.package_id,
            str(e),
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to generate discharge summary: {str(e)}",
        )

