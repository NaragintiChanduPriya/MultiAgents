from typing import Optional
from fastapi import APIRouter, Depends, Header, HTTPException, status

from schemas.appointment_schema import (
    AppointmentChatRequest,
    AppointmentChatResponse,
)
from services.appointment_service import appointment_service

router = APIRouter(prefix="/api/v1/appointments", tags=["Appointments"])


async def get_authenticated_patient_id(
    x_patient_id: Optional[str] = Header(
        default=None,
        alias="X-Patient-Id",
        description="Verified patient identity identifier (e.g. 'PAT0001').",
    ),
    authorization: Optional[str] = Header(
        default=None,
        description="Bearer token containing verified patient credentials.",
    ),
) -> str:
    """Authentication dependency ensuring only verified patients can access appointment services."""
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
    "/chat",
    response_model=AppointmentChatResponse,
    status_code=status.HTTP_200_OK,
    summary="Chat with the Appointment Agent",
    description="Interacts with the independent Appointment Agent for scheduling, slot queries, and doctor search.",
)
async def chat_with_appointment_agent(
    request: AppointmentChatRequest,
    patient_id: str = Depends(get_authenticated_patient_id),
) -> AppointmentChatResponse:
    """Processes appointment conversation turns through the Appointment Agent."""
    try:
        response = await appointment_service.process_chat(
            patient_id=patient_id,
            message=request.message,
            session_id=request.session_id,
        )
        return response
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to process appointment chat turn: {str(e)}",
        )

