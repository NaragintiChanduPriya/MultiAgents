from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from services.discharge_summary_service import DischargeSummaryService


@pytest.fixture
def sample_package_tool_result():
    return {
        "status": "success",
        "found": True,
        "package": {
            "package_id": "DP0001",
            "admission_id": "ADM0001",
            "package_status": "Approved",
            "initiated_at": "2026-01-03T12:00:00",
            "completed_at": "2026-01-04T10:00:00",
            "clinical_summary": "Asthma exacerbation successfully stabilized.",
            "follow_up_plan": "Specialist visit in 2 weeks",
            "patient_instructions": "Continue inhaled bronchodilator",
            "medication_reconciliation_status": "No flags",
            "medication_flags_count": 0,
            "safety_validation_status": "Passed",
            "physician_review_status": "Approved",
            "pharmacist_review_status": "Approved",
            "nurse_review_status": "Approved",
            "confidence_score": 0.95,
        },
        "admission": {
            "admission_id": "ADM0001",
            "patient_id": "PAT0019",
            "admission_date": "2026-01-03",
            "discharge_date": "2026-01-04",
            "admitting_diagnosis": "Asthma exacerbation",
            "icd10_code": "J45.901",
            "bed_number": "ICU-04",
            "admission_status": "Discharged",
        },
        "patient": {
            "patient_id": "PAT0019",
            "first_name": "Varun",
            "last_name": "Joshi",
            "gender": "Male",
            "blood_group": "AB-",
            "allergies": "None known",
        },
        "medications": [
            {
                "medication_record_id": "MED000001",
                "medication_name": "Salbutamol",
                "drug_class": "Bronchodilator",
                "strength": "100 mcg",
                "route": "Inhalation",
                "dose_instructions": "As needed",
                "medication_type": "Home",
                "medication_status": "Active",
            }
        ],
        "procedures": [],
        "lab_results": [
            {"test_name": "Hemoglobin", "result_value": "14.2", "result_unit": "g/dL", "abnormal_flag": "Normal"}
        ],
        "clinical_notes": [],
        "vitals": [],
        "missing_information": ["No procedures documented."],
    }


@pytest.mark.asyncio
async def test_service_successful_report_generation(sample_package_tool_result):
    """Service coordinates tool lookup and generates discharge summary via agent."""
    service = DischargeSummaryService()

    with patch(
        "services.discharge_summary_service.get_discharge_package_by_id",
        return_value=sample_package_tool_result,
    ):
        mock_event = MagicMock()
        mock_part = MagicMock()
        mock_part.text = "## 1. Report Overview\nPackage ID: DP0001\nStatus: Approved"
        mock_event.content.parts = [mock_part]

        async def mock_run_async(*args, **kwargs):
            yield mock_event

        with patch.object(service.runner, "run_async", side_effect=mock_run_async):
            res = await service.generate_discharge_summary(
                package_id="DP0001",
                authenticated_patient_id="PAT0019",
            )

            assert res.status == "success"
            assert res.package_id == "DP0001"
            assert res.patient_id == "PAT0019"
            assert "Report Overview" in res.report
            assert res.source_data is not None
            assert res.source_data.package["package_status"] == "Approved"
            assert res.source_data.patient["first_name"] == "Varun"
            assert len(res.missing_information) == 1


@pytest.mark.asyncio
async def test_service_not_found_skips_llm():
    """Non-existent package returns not_found status and never invokes the LLM."""
    service = DischargeSummaryService()

    with patch(
        "services.discharge_summary_service.get_discharge_package_by_id",
        return_value={"status": "not_found", "found": False, "package": None},
    ):
        with patch.object(service.runner, "run_async") as mock_runner:
            res = await service.generate_discharge_summary(
                package_id="DP-MISSING",
                authenticated_patient_id="PAT0019",
            )

            assert res.status == "not_found"
            assert res.source_data is None
            assert "No discharge package found" in res.report
            mock_runner.assert_not_called()


@pytest.mark.asyncio
async def test_service_enforces_patient_authorization(sample_package_tool_result):
    """Patient cannot access discharge packages belonging to another patient."""
    service = DischargeSummaryService()

    with patch(
        "services.discharge_summary_service.get_discharge_package_by_id",
        return_value=sample_package_tool_result,
    ):
        with patch.object(service.runner, "run_async") as mock_runner:
            # Caller is PAT0001, but package belongs to PAT0019
            res = await service.generate_discharge_summary(
                package_id="DP0001",
                authenticated_patient_id="PAT0001",
            )

            assert res.status == "forbidden"
            assert "Access denied" in res.error
            assert res.source_data is None
            mock_runner.assert_not_called()


@pytest.mark.asyncio
async def test_service_empty_identifier_fails_fast():
    """Empty or invalid package ID returns error immediately."""
    service = DischargeSummaryService()
    res = await service.generate_discharge_summary(package_id="")
    assert res.status == "error"
    assert "required" in res.error
    assert res.source_data is None


@pytest.mark.asyncio
async def test_service_database_error_handled_gracefully():
    """Database connection failure returns structured error."""
    service = DischargeSummaryService()

    with patch(
        "services.discharge_summary_service.get_discharge_package_by_id",
        return_value={"status": "error", "message": "Connection refused to PostgreSQL.", "package": None},
    ):
        res = await service.generate_discharge_summary(package_id="DP0001")
        assert res.status == "error"
        assert "Connection refused" in res.error
        assert res.source_data is None


@pytest.mark.asyncio
async def test_service_llm_failure_falls_back_to_deterministic_report(sample_package_tool_result):
    """If the LLM fails, service falls back to a deterministic report using verified database facts."""
    service = DischargeSummaryService()

    with patch(
        "services.discharge_summary_service.get_discharge_package_by_id",
        return_value=sample_package_tool_result,
    ):
        async def mock_run_error(*args, **kwargs):
            raise RuntimeError("Groq API quota or rate limit exceeded")
            yield  # unreachable

        with patch.object(service.runner, "run_async", side_effect=mock_run_error):
            res = await service.generate_discharge_summary(
                package_id="DP0001",
                authenticated_patient_id="PAT0019",
            )

            assert res.status == "success"
            assert res.source_data is not None
            # Fallback report should include verified facts
            assert "DP0001" in res.report
            assert "Varun Joshi" in res.report
            assert "Asthma exacerbation" in res.report
            assert "Salbutamol" in res.report

