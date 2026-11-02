from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from services.medication_service import MedicationService


@pytest.fixture
def sample_med_record():
    return {
        "medication_record_id": "MED000001",
        "admission_id": "ADM0001",
        "patient_id": "PAT0019",
        "medication_name": "Salbutamol",
        "drug_class": "Bronchodilator",
        "strength": "100 mcg",
        "strength_value": 100.0,
        "strength_unit": "mcg",
        "route": "Inhalation",
        "medication_type": "Home",
        "dose_instructions": "As needed",
        "medication_status": "Stopped",
    }


@pytest.mark.asyncio
async def test_service_successful_lookup_and_explanation(sample_med_record):
    """Service coordinates tool lookup and generates explanation via agent."""
    service = MedicationService()

    # Mock database tool
    with patch(
        "services.medication_service.get_medication_by_record_id",
        return_value={"status": "success", "found": True, "record": sample_med_record},
    ):
        # Mock runner.run_async to yield simulated agent events
        mock_event = MagicMock()
        mock_part = MagicMock()
        mock_part.text = "Salbutamol is a bronchodilator inhaler used for asthma relief."
        mock_event.content.parts = [mock_part]

        async def mock_run_async(*args, **kwargs):
            yield mock_event

        with patch.object(service.runner, "run_async", side_effect=mock_run_async):
            res = await service.get_medication_details(
                medication_record_id="MED000001",
                authenticated_patient_id="PAT0019",
            )

            assert res.status == "success"
            assert res.medication_record_id == "MED000001"
            assert res.patient_id == "PAT0019"
            assert res.record is not None
            assert res.record.medication_name == "Salbutamol"
            assert "Salbutamol is a bronchodilator" in res.explanation
            assert res.error is None


@pytest.mark.asyncio
async def test_service_not_found_skips_llm():
    """Non-existent records must return not_found and never invoke the LLM."""
    service = MedicationService()

    with patch(
        "services.medication_service.get_medication_by_record_id",
        return_value={"status": "not_found", "found": False, "record": None},
    ):
        with patch.object(service.runner, "run_async") as mock_runner:
            res = await service.get_medication_details(
                medication_record_id="MED-NONEXISTENT",
                authenticated_patient_id="PAT0019",
            )

            assert res.status == "not_found"
            assert res.record is None
            assert "No medication record matches" in res.explanation
            # Verify LLM was NOT invoked
            mock_runner.assert_not_called()


@pytest.mark.asyncio
async def test_service_enforces_patient_authorization(sample_med_record):
    """Patient cannot access medication records belonging to another patient."""
    service = MedicationService()

    with patch(
        "services.medication_service.get_medication_by_record_id",
        return_value={"status": "success", "found": True, "record": sample_med_record},
    ):
        with patch.object(service.runner, "run_async") as mock_runner:
            # Caller is PAT0001, but record belongs to PAT0019
            res = await service.get_medication_details(
                medication_record_id="MED000001",
                authenticated_patient_id="PAT0001",
            )

            assert res.status == "forbidden"
            assert "Access denied" in res.error
            assert res.record is None
            mock_runner.assert_not_called()


@pytest.mark.asyncio
async def test_service_empty_identifier_fails_fast():
    """Empty or invalid identifier returns error response immediately."""
    service = MedicationService()
    res = await service.get_medication_details(medication_record_id="")
    assert res.status == "error"
    assert "required" in res.error
    assert res.record is None


@pytest.mark.asyncio
async def test_service_database_error_handled_gracefully():
    """Database errors return a structured error status."""
    service = MedicationService()

    with patch(
        "services.medication_service.get_medication_by_record_id",
        return_value={"status": "error", "message": "Database connection refused.", "record": None},
    ):
        res = await service.get_medication_details(medication_record_id="MED000001")
        assert res.status == "error"
        assert "Database connection refused" in res.error
        assert res.record is None


@pytest.mark.asyncio
async def test_service_llm_failure_falls_back_to_deterministic_explanation(sample_med_record):
    """If the LLM fails, service falls back to a deterministic explanation based on database facts."""
    service = MedicationService()

    with patch(
        "services.medication_service.get_medication_by_record_id",
        return_value={"status": "success", "found": True, "record": sample_med_record},
    ):
        # LLM raises an unexpected exception
        async def mock_run_error(*args, **kwargs):
            raise RuntimeError("Groq API quota exceeded or timeout")
            yield  # unreachable

        with patch.object(service.runner, "run_async", side_effect=mock_run_error):
            res = await service.get_medication_details(
                medication_record_id="MED000001",
                authenticated_patient_id="PAT0019",
            )

            assert res.status == "success"
            assert res.record is not None
            assert res.record.medication_name == "Salbutamol"
            # Fallback explanation should still be present and accurate
            assert "Salbutamol" in res.explanation
            assert "Bronchodilator" in res.explanation
            assert "100 mcg" in res.explanation

