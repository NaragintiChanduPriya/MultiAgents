from unittest.mock import AsyncMock, patch
import pytest
from fastapi.testclient import TestClient

from agents.discharge_summary_report_agent import discharge_summary_report_agent
from main import app
from schemas.discharge_summary_schema import (
    DischargePackageSourceData,
    DischargeSummaryResponse,
)

client = TestClient(app)


@pytest.fixture
def sample_response_data():
    source_data = DischargePackageSourceData(
        package={
            "package_id": "DP0001",
            "admission_id": "ADM0001",
            "package_status": "Approved",
        },
        admission={
            "admission_id": "ADM0001",
            "patient_id": "PAT0019",
            "admitting_diagnosis": "Asthma exacerbation",
        },
        patient={
            "patient_id": "PAT0019",
            "first_name": "Varun",
            "last_name": "Joshi",
        },
        medications=[],
        procedures=[],
        lab_results=[],
        clinical_notes=[],
        vitals=[],
    )
    return DischargeSummaryResponse(
        package_id="DP0001",
        status="success",
        report="## 1. Report Overview\nPackage ID: DP0001\nStatus: Approved",
        source_data=source_data,
        missing_information=["No procedures recorded."],
        patient_id="PAT0019",
        error=None,
    )


def test_discharge_summary_requires_authentication():
    """Endpoint rejects unauthenticated requests with 401 Unauthorized."""
    response = client.post(
        "/api/v1/discharge-summaries/generate",
        json={"package_id": "DP0001"},
    )
    assert response.status_code == 401
    assert "Authentication required" in response.json()["detail"]


def test_discharge_summary_validates_request_body():
    """Empty or whitespace package ID is rejected with 422 Unprocessable Entity."""
    response = client.post(
        "/api/v1/discharge-summaries/generate",
        headers={"X-Patient-Id": "PAT0019"},
        json={"package_id": ""},
    )
    assert response.status_code == 422


def test_discharge_summary_success_with_patient_id_header(sample_response_data):
    """Authorized patient successfully retrieves and generates discharge summary."""
    with patch(
        "routes.discharge_summary_routes.discharge_summary_service.generate_discharge_summary",
        new=AsyncMock(return_value=sample_response_data),
    ):
        response = client.post(
            "/api/v1/discharge-summaries/generate",
            headers={"X-Patient-Id": "PAT0019"},
            json={"package_id": "DP0001"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "success"
        assert data["package_id"] == "DP0001"
        assert data["source_data"]["package"]["package_id"] == "DP0001"
        assert "Report Overview" in data["report"]


def test_discharge_summary_success_with_bearer_token(sample_response_data):
    """Bearer token authentication is supported and accepted."""
    with patch(
        "routes.discharge_summary_routes.discharge_summary_service.generate_discharge_summary",
        new=AsyncMock(return_value=sample_response_data),
    ):
        response = client.post(
            "/api/v1/discharge-summaries/generate",
            headers={"Authorization": "Bearer PAT0019"},
            json={"package_id": "DP0001"},
        )
        assert response.status_code == 200
        assert response.json()["package_id"] == "DP0001"


def test_discharge_summary_forbidden_when_unauthorized():
    """Forbidden status returned when patient tries to access another patient's package."""
    forbidden_resp = DischargeSummaryResponse(
        package_id="DP0001",
        status="forbidden",
        error="Access denied. You are not authorized to view discharge records for another patient.",
        report="",
        source_data=None,
    )
    with patch(
        "routes.discharge_summary_routes.discharge_summary_service.generate_discharge_summary",
        new=AsyncMock(return_value=forbidden_resp),
    ):
        response = client.post(
            "/api/v1/discharge-summaries/generate",
            headers={"X-Patient-Id": "PAT0001"},
            json={"package_id": "DP0001"},
        )
        assert response.status_code == 403
        assert "Access denied" in response.json()["detail"]


def test_discharge_summary_not_found():
    """Returns 404 Not Found when package_id does not exist."""
    not_found_resp = DischargeSummaryResponse(
        package_id="DP-UNKNOWN",
        status="not_found",
        error="Discharge package 'DP-UNKNOWN' was not found in hospital database records.",
        report="No matching package found.",
        source_data=None,
    )
    with patch(
        "routes.discharge_summary_routes.discharge_summary_service.generate_discharge_summary",
        new=AsyncMock(return_value=not_found_resp),
    ):
        response = client.post(
            "/api/v1/discharge-summaries/generate",
            headers={"X-Patient-Id": "PAT0019"},
            json={"package_id": "DP-UNKNOWN"},
        )
        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()


def test_discharge_summary_agent_is_independent():
    """Verify architectural independence: no parent agent, no sub-agents."""
    assert discharge_summary_report_agent.parent_agent is None
    assert getattr(discharge_summary_report_agent, "sub_agents", []) == []
    assert discharge_summary_report_agent.name == "discharge_summary_report_agent"


def test_regression_existing_agents_still_work():
    """Regression test ensuring Appointment Agent, Medication Agent, and health check still work."""
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

