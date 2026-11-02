from unittest.mock import AsyncMock, patch
import pytest
from fastapi.testclient import TestClient

from main import app
from schemas.appointment_schema import AppointmentChatResponse

client = TestClient(app)


def test_health_endpoint():
    """Health check endpoint should return 200 OK and service status."""
    with patch("main.check_db_health", return_value=(True, None)):
        response = client.get("/api/v1/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"
        assert data["database_connected"] is True
        assert data["database_error"] is None


def test_health_endpoint_degraded_when_db_down():
    """Health check endpoint returns degraded status when database is down."""
    with patch("main.check_db_health", return_value=(False, "Connection refused")):
        response = client.get("/api/v1/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "degraded"
        assert data["database_connected"] is False
        assert data["database_error"] == "Connection refused"


def test_appointment_chat_requires_authentication():
    """Chat endpoint rejects requests without patient identification with 401."""
    response = client.post(
        "/api/v1/appointments/chat",
        json={"message": "I need to see a cardiologist"},
    )
    assert response.status_code == 401
    assert "Authentication required" in response.json()["detail"]


def test_appointment_chat_validates_request_body():
    """Empty message is rejected with 422 Unprocessable Entity."""
    response = client.post(
        "/api/v1/appointments/chat",
        headers={"X-Patient-Id": "PAT0001"},
        json={"message": ""},
    )
    assert response.status_code == 422


def test_appointment_chat_successful_turn():
    """Authenticated request calls appointment service and returns structured response."""
    mock_service_response = AppointmentChatResponse(
        session_id="sess_12345",
        response="We have Dr. Surya Kumar and Dr. Varun Mehta available in Cardiology.",
        status="success",
    )

    with patch(
        "routes.appointment_routes.appointment_service.process_chat",
        new=AsyncMock(return_value=mock_service_response),
    ) as mock_process:
        response = client.post(
            "/api/v1/appointments/chat",
            headers={"X-Patient-Id": "PAT0001"},
            json={
                "message": "Which cardiologists are available?",
                "session_id": "sess_12345",
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "success"
        assert data["session_id"] == "sess_12345"
        assert "Surya Kumar" in data["response"]

        # Ensure service was invoked with the verified patient identity
        mock_process.assert_awaited_once_with(
            patient_id="PAT0001",
            message="Which cardiologists are available?",
            session_id="sess_12345",
        )


def test_appointment_chat_bearer_token_authentication():
    """Patient ID supplied via Authorization: Bearer <id> is accepted."""
    mock_service_response = AppointmentChatResponse(
        session_id="sess_abcde",
        response="Your appointment is confirmed.",
        status="success",
    )

    with patch(
        "routes.appointment_routes.appointment_service.process_chat",
        new=AsyncMock(return_value=mock_service_response),
    ):
        response = client.post(
            "/api/v1/appointments/chat",
            headers={"Authorization": "Bearer PAT0002"},
            json={"message": "Confirm booking"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "success"
        assert data["session_id"] == "sess_abcde"


def test_appointment_agent_is_independent():
    """Verify that Appointment Agent has no parent_agent, no sub_agents, and no orchestrator."""
    from agents.appointment_agent import appointment_agent

    # Rule: No parent agent
    assert appointment_agent.parent_agent is None
    # Rule: No sub-agents
    assert appointment_agent.sub_agents == []
    # Rule: Tools are registered directly
    tool_names = {t.__name__ for t in appointment_agent.tools}
    assert tool_names == {
        "inspect_database_schema",
        "list_specialties",
        "get_doctors_by_specialty",
        "get_doctor_details",
        "get_available_doctor_slots",
        "check_slot_availability",
        "get_appointment_details",
        "get_patient_appointments",
        "create_appointment",
        "cancel_appointment",
    }

