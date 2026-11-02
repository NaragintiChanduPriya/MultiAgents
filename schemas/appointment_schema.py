from typing import Optional
from pydantic import BaseModel, Field


class AppointmentChatRequest(BaseModel):
    """Chat request model sent by the patient or client."""
    message: str = Field(..., min_length=1, description="Message text from the patient.")
    session_id: Optional[str] = Field(
        default=None,
        description="Optional existing session ID. If omitted, a new conversation session will be created.",
    )


class AppointmentChatResponse(BaseModel):
    """Standardized chat response model returned to the client."""
    session_id: str = Field(..., description="The conversational session identifier.")
    response: str = Field(..., description="The assistant's text response.")
    status: str = Field(default="success", description="Status of the request ('success' or 'error').")
    error: Optional[str] = Field(default=None, description="Error message if the request failed.")


class HealthResponse(BaseModel):
    """System health check response."""
    status: str = Field(..., description="Overall service status ('healthy' or 'degraded').")
    app_name: str = Field(..., description="Application name.")
    database_connected: bool = Field(..., description="Whether MongoDB is connected and responsive.")
    database_error: Optional[str] = Field(default=None, description="Sanitized database error if disconnected.")

