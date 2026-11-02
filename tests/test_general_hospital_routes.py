from unittest.mock import AsyncMock, patch
import pytest
from fastapi.testclient import TestClient

from agents.general_hospital_agent import general_hospital_agent
from main import app
from schemas.general_hospital_schema import GeneralHospitalResponse

client = TestClient(app)


@pytest.fixture
def sample_response():
    return GeneralHospitalResponse(
        question="What departments are available?",
        status="success",
        answer="The hospital has Cardiology, Orthopedics, Neurology, Pulmonology, and General Medicine departments.",
        sources=["PostgreSQL: doctors and beds tables"],
        session_id="gen_sess_12345",
    )


def test_ask_general_hospital_validates_request_body():
    """Empty or whitespace question is rejected with 422 Unprocessable Entity."""
    response = client.post(
        "/api/v1/hospital/ask",
        json={"question": "   "},
    )
    assert response.status_code == 422


def test_ask_general_hospital_guest_success(sample_response):
    """General hospital questions can be asked by prospective patients / visitors without mandatory auth."""
    with patch(
        "routes.general_hospital_routes.general_hospital_service.answer_question",
        new=AsyncMock(return_value=sample_response),
    ):
        response = client.post(
            "/api/v1/hospital/ask",
            json={"question": "What departments are available?"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "success"
        assert "Cardiology" in data["answer"]
        assert len(data["sources"]) > 0


def test_ask_general_hospital_with_patient_header(sample_response):
    """Optional patient identity header is accepted and tracked."""
    with patch(
        "routes.general_hospital_routes.general_hospital_service.answer_question",
        new=AsyncMock(return_value=sample_response),
    ):
        response = client.post(
            "/api/v1/hospital/ask",
            headers={"X-Patient-Id": "PAT0019"},
            json={"question": "What departments are available?"},
        )
        assert response.status_code == 200
        assert response.json()["status"] == "success"


def test_ask_general_hospital_with_bearer_token(sample_response):
    """Optional Bearer token is supported."""
    with patch(
        "routes.general_hospital_routes.general_hospital_service.answer_question",
        new=AsyncMock(return_value=sample_response),
    ):
        response = client.post(
            "/api/v1/hospital/ask",
            headers={"Authorization": "Bearer PAT0019"},
            json={"question": "What departments are available?"},
        )
        assert response.status_code == 200


def test_ask_general_hospital_emergency_alert():
    """Emergency symptoms return immediate emergency alert response."""
    emergency_resp = GeneralHospitalResponse(
        question="I have crushing chest pain and difficulty breathing",
        status="emergency_alert",
        answer="EMERGENCY ADVISORY: Please immediately call 108 or visit the Emergency Room.",
        sources=["Hospital Emergency Triage Protocol"],
    )
    with patch(
        "routes.general_hospital_routes.general_hospital_service.answer_question",
        new=AsyncMock(return_value=emergency_resp),
    ):
        response = client.post(
            "/api/v1/hospital/ask",
            json={"question": "I have crushing chest pain and difficulty breathing"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "emergency_alert"
        assert "108" in data["answer"]


def test_general_hospital_agent_is_independent():
    """Verify architectural independence: no parent agent, no sub-agents."""
    assert general_hospital_agent.parent_agent is None
    assert getattr(general_hospital_agent, "sub_agents", []) == []
    assert general_hospital_agent.name == "general_hospital_agent"


def test_regression_all_existing_agents_still_work():
    """Regression test ensuring Appointment, Medication, Discharge Summary, and Health check remain intact."""
    # Health check
    with patch("main.check_db_health", return_value=(True, None)):
        health_resp = client.get("/api/v1/health")
        assert health_resp.status_code == 200
        assert health_resp.json()["status"] == "healthy"

    # Appointment chat route remains operational
    unauth_appt = client.post("/api/v1/appointments/chat", json={"message": "hello"})
    assert unauth_appt.status_code == 401

    # Medication details route remains operational
    unauth_med = client.post("/api/v1/medications/details", json={"medication_record_id": "MED000001"})
    assert unauth_med.status_code == 401

    # Discharge summary route remains operational
    unauth_ds = client.post("/api/v1/discharge-summaries/generate", json={"package_id": "DP0001"})
    assert unauth_ds.status_code == 401

