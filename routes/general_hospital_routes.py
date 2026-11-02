import logging
from typing import Optional
from fastapi import APIRouter, Header, HTTPException, status

from schemas.general_hospital_schema import (
    GeneralHospitalRequest,
    GeneralHospitalResponse,
)
from services.general_hospital_service import general_hospital_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/hospital", tags=["General Hospital Information"])


def get_optional_user_id(
    x_patient_id: Optional[str] = Header(
        default=None,
        alias="X-Patient-Id",
        description="Optional patient identity for personalized context.",
    ),
    authorization: Optional[str] = Header(
        default=None,
        description="Optional Bearer token.",
    ),
) -> str:
    """Optional authentication: supports logged-in patients while allowing guest inquiries."""
    if x_patient_id and x_patient_id.strip():
        return x_patient_id.strip()

    if authorization and authorization.strip().startswith("Bearer "):
        token = authorization.strip()[7:].strip()
        if token:
            return token

    return "guest_visitor"


@router.post(
    "/ask",
    response_model=GeneralHospitalResponse,
    status_code=status.HTTP_200_OK,
    summary="Ask a general hospital question",
    description="Answers non-specialized questions regarding hospital departments, services, visiting hours, and policies using verified facts.",
)
async def ask_general_hospital_question(
    request: GeneralHospitalRequest,
    x_patient_id: Optional[str] = Header(default=None, alias="X-Patient-Id"),
    authorization: Optional[str] = Header(default=None),
) -> GeneralHospitalResponse:
    """Handles general hospital questions with emergency screening and source verification."""
    user_id = get_optional_user_id(x_patient_id, authorization)

    try:
        response = await general_hospital_service.answer_question(
            question=request.question,
            user_id=user_id,
            session_id=request.session_id,
        )
        return response

    except Exception as e:
        logger.error("Failed to answer general hospital question: %s", str(e))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"An error occurred while processing your hospital inquiry: {str(e)}",
        )

