from unittest.mock import AsyncMock, patch
import pytest
from fastapi.testclient import TestClient

from agents.medication_agent import medication_agent
from main import app
from schemas.medication_schema import (
    MedicationDetailResponse,
    MedicationRecordItem,
)

client = TestClient(app)


@pytest.fixture
def sample_response_data():
    record_item = MedicationRecordItem(
        medication_record_id="MED000001",
        admission_id="ADM0001",
        patient_id="PAT0019",
        medication_name="Salbutamol",
        drug_class="Bronchodilator",
        strength="100 mcg",
        strength_value=100.0,
        strength_unit="mcg",
        route="Inhalation",
        medication_type="Home",
        dose_instructions="As needed",
        medication_status="Stopped",
    )
    return MedicationDetailResponse(
        medication_record_id="MED000001",
        status="success",
        record=record_item,
        explanation="Salbutamol is a bronchodilator inhaler used for respiratory relief.",
        patient_id="PAT0019",
        error=None,
    )


def test_medication_details_requires_authentication():
    """Endpoint rejects unauthenticated requests with 401 Unauthorized."""
    response = client.post(
        "/api/v1/medications/details",
        json={"medication_record_id": "MED000001"},
    )
    assert response.status_code == 401
    assert "Authentication required" in response.json()["detail"]


def test_medication_details_validates_request_body():
    """Empty or whitespace identifier is rejected with 422 Unprocessable Entity."""
    response = client.post(
        "/api/v1/medications/details",
        headers={"X-Patient-Id": "PAT0019"},
        json={"medication_record_id": ""},
    )
    assert response.status_code == 422


def test_medication_details_success_with_patient_id_header(sample_response_data):
    """Authorized patient successfully retrieves medication details."""
    with patch(
        "routes.medication_routes.medication_service.get_medication_details",
        new=AsyncMock(return_value=sample_response_data),
    ):
        response = client.post(
            "/api/v1/medications/details",
            headers={"X-Patient-Id": "PAT0019"},
            json={"medication_record_id": "MED000001"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "success"
        assert data["medication_record_id"] == "MED000001"
        assert data["record"]["medication_name"] == "Salbutamol"
        assert data["record"]["strength"] == "100 mcg"
        assert "bronchodilator" in data["explanation"]


def test_medication_details_success_with_bearer_token(sample_response_data):
    """Bearer token authentication is supported and accepted."""
    with patch(
        "routes.medication_routes.medication_service.get_medication_details",
        new=AsyncMock(return_value=sample_response_data),
    ):
        response = client.post(
            "/api/v1/medications/details",
            headers={"Authorization": "Bearer PAT0019"},
            json={"medication_record_id": "MED000001"},
        )
        assert response.status_code == 200
        assert response.json()["medication_record_id"] == "MED000001"


def test_medication_details_forbidden_when_unauthorized():
    """Forbidden status returned when patient tries to access another patient's medication."""
    forbidden_resp = MedicationDetailResponse(
        medication_record_id="MED000001",
        status="forbidden",
        error="Access denied. You are not authorized to view medication records for another patient.",
        explanation="",
        record=None,
    )
    with patch(
        "routes.medication_routes.medication_service.get_medication_details",
        new=AsyncMock(return_value=forbidden_resp),
    ):
        response = client.post(
            "/api/v1/medications/details",
            headers={"X-Patient-Id": "PAT0001"},
            json={"medication_record_id": "MED000001"},
        )
        assert response.status_code == 403
        assert "Access denied" in response.json()["detail"]


def test_medication_details_not_found():
    """Returns 404 Not Found when medication_record_id does not exist."""
    not_found_resp = MedicationDetailResponse(
        medication_record_id="MED-UNKNOWN",
        status="not_found",
        error="Medication record 'MED-UNKNOWN' was not found in hospital database records.",
        explanation="No matching record found.",
        record=None,
    )
    with patch(
        "routes.medication_routes.medication_service.get_medication_details",
        new=AsyncMock(return_value=not_found_resp),
    ):
        response = client.post(
            "/api/v1/medications/details",
            headers={"X-Patient-Id": "PAT0019"},
            json={"medication_record_id": "MED-UNKNOWN"},
        )
        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()


def test_medication_agent_is_independent():
    """Verify architectural independence: no parent agent, no sub-agents."""
    assert medication_agent.parent_agent is None
    assert getattr(medication_agent, "sub_agents", []) == []
    assert medication_agent.name == "medication_agent"


def test_regression_appointment_routes_and_health_still_work():
    """Regression test ensuring Appointment Agent and health routes are completely intact."""
    with patch("main.check_db_health", return_value=(True, None)):
        health_resp = client.get("/api/v1/health")
        assert health_resp.status_code == 200
        assert health_resp.json()["status"] == "healthy"

    # Appointment chat route remains operational
    unauth_resp = client.post("/api/v1/appointments/chat", json={"message": "hello"})
    assert unauth_resp.status_code == 401

