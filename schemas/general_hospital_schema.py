from typing import List, Optional
from pydantic import BaseModel, Field, field_validator


class GeneralHospitalRequest(BaseModel):
    """Request model for asking general hospital-related questions."""

    question: str = Field(
        ...,
        min_length=1,
        max_length=1000,
        description="General question about hospital services, departments, visiting hours, or policies.",
        examples=["What departments are available in this hospital?"],
    )
    session_id: Optional[str] = Field(
        None,
        description="Optional session identifier to maintain conversational context.",
    )

    @field_validator("question")
    @classmethod
    def validate_question(cls, v: str) -> str:
        clean = v.strip()
        if not clean:
            raise ValueError("question cannot be blank or whitespace.")
        return clean


class GeneralHospitalResponse(BaseModel):
    """Response model returning verified information and educational hospital guidance."""

    question: str = Field(..., description="The user's submitted inquiry.")
    status: str = Field(
        "success",
        description="Outcome status: 'success', 'emergency_alert', 'clarification_needed', or 'error'.",
    )
    answer: str = Field(
        ...,
        description="Verified answer or clinical redirection provided by the General Hospital Assistant Agent.",
    )
    sources: List[str] = Field(
        default_factory=list,
        description="List of verified data sources consulted (e.g. MongoDB collections, institutional policies).",
    )
    session_id: Optional[str] = Field(
        None,
        description="Active conversation session identifier.",
    )
    error: Optional[str] = Field(
        None,
        description="Error details if the request could not be fulfilled.",
    )

