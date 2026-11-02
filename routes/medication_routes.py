import logging
from typing import Optional
from fastapi import APIRouter, Depends, Header, HTTPException, status

from schemas.medication_schema import (
    MedicationDetailRequest,
    MedicationDetailResponse,
    PrescriptionCounsellingRequest,
    PrescriptionCounsellingResponse,
)
from services.medication_service import medication_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/medications", tags=["Medications"])


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
    """Authentication dependency ensuring only authenticated callers can access medication services."""
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
    "/details",
    response_model=MedicationDetailResponse,
    status_code=status.HTTP_200_OK,
    summary="Get and explain medication details",
    description="Retrieves a verified medication record from PostgreSQL by medication_record_id and generates a clear educational explanation via the Medication Agent.",
)
async def get_medication_details(
    request: MedicationDetailRequest,
    caller_id: str = Depends(get_authenticated_user_id),
) -> MedicationDetailResponse:
    """Handles medication record lookup and explanation with authorization enforcement."""
    try:
        response = await medication_service.get_medication_details(
            medication_record_id=request.medication_record_id,
            authenticated_patient_id=caller_id,
            session_id=request.session_id,
        )

        if response.status == "forbidden":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=response.error or "Access denied to this medication record.",
            )

        if response.status == "not_found":
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=response.error or f"Medication record '{request.medication_record_id}' not found.",
            )

        if response.status == "error":
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=response.error or "An error occurred while processing the medication record.",
            )

        return response

    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            "Unexpected error handling medication record %s: %s",
            request.medication_record_id,
            str(e),
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to process medication request: {str(e)}",
        )



@router.post(
    "/counselling",
    response_model=PrescriptionCounsellingResponse,
    status_code=status.HTTP_200_OK,
    summary="Write the take-home counselling sheet for a prescription",
    description=(
        "Reads a verified prescription by prescription_id and writes the plain-language "
        "sheet the patient takes home from the pharmacy. The medicines, doses and "
        "durations come from the database; the agent only phrases them."
    ),
)
async def generate_prescription_counselling(
    request: PrescriptionCounsellingRequest,
    caller_id: str = Depends(get_authenticated_user_id),
) -> PrescriptionCounsellingResponse:
    """Handles counselling generation with ownership enforcement."""
    try:
        response = await medication_service.generate_counselling(
            prescription_id=request.prescription_id,
            authenticated_patient_id=caller_id,
            session_id=request.session_id,
        )

        if response.status == "forbidden":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=response.error or "Access denied to this prescription.",
            )

        if response.status == "not_found":
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=response.error
                or f"Prescription '{request.prescription_id}' not found.",
            )

        if response.status == "error":
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=response.error or "Could not write the counselling sheet.",
            )

        return response

    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            "Unexpected error counselling on prescription %s: %s",
            request.prescription_id,
            str(e),
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to generate counselling: {str(e)}",
        )
