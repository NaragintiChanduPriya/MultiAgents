from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from services.general_hospital_service import GeneralHospitalService


@pytest.mark.asyncio
async def test_service_emergency_screening():
    """Emergency symptoms trigger an immediate priority alert without calling the LLM."""
    service = GeneralHospitalService()

    emergency_questions = [
        "I am having severe chest pain and cannot breathe",
        "My mother collapsed and is unconscious",
        "Sudden weakness on one side of face with slurred speech",
    ]

    for q in emergency_questions:
        res = await service.answer_question(q)
        assert res.status == "emergency_alert"
        assert "EMERGENCY ADVISORY" in res.answer
        # The number must be the hospital's own, matching what the website
        # and the policy documents tell people to dial.
        assert "108" in res.answer
        assert "Hospital Emergency Triage Protocol" in res.sources


@pytest.mark.asyncio
async def test_service_answers_department_inquiry():
    """Service gathers department context and returns agent response."""
    service = GeneralHospitalService()

    with patch(
        "services.general_hospital_service.search_hospital_departments",
        return_value={
            "status": "success",
            "departments": [
                {
                    "department": "Cardiology",
                    "slug": "cardiology",
                    "doctor_count": 2,
                    "tagline": "Heart and rhythm care.",
                    "common_conditions": ["Heart failure"],
                    "location": "Second floor, Block B",
                    "consultation_fee_inr": 800,
                },
                {
                    "department": "Orthopaedics",
                    "slug": "orthopaedics",
                    "doctor_count": 1,
                    "tagline": "Bones, joints and movement.",
                    "common_conditions": ["Osteoarthritis"],
                    "location": "First floor, Block C",
                    "consultation_fee_inr": 700,
                },
            ],
        },
    ):
        mock_event = MagicMock()
        mock_part = MagicMock()
        mock_part.text = "Our hospital offers Cardiology and Orthopaedics departments with active specialists."
        mock_event.content.parts = [mock_part]

        async def mock_run_async(*args, **kwargs):
            yield mock_event

        with patch.object(service.runner, "run_async", side_effect=mock_run_async):
            res = await service.answer_question("What departments are available in this hospital?")
            assert res.status == "success"
            assert "Cardiology" in res.answer
            assert any("specialties" in s for s in res.sources)


@pytest.mark.asyncio
async def test_service_answers_registration_policy_inquiry():
    """Service retrieves verified registration requirements for new patient inquiry."""
    service = GeneralHospitalService()

    mock_event = MagicMock()
    mock_part = MagicMock()
    mock_part.text = "To register as a new patient, please bring a valid government photo ID and proof of address."
    mock_event.content.parts = [mock_part]

    async def mock_run_async(*args, **kwargs):
        yield mock_event

    with patch.object(service.runner, "run_async", side_effect=mock_run_async):
        res = await service.answer_question("How can I register as a new patient and what documents should I bring?")
        assert res.status == "success"
        assert "photo ID" in res.answer
        assert any("Policies" in s for s in res.sources)


@pytest.mark.asyncio
async def test_service_llm_failure_falls_back_to_deterministic_facts():
    """If the LLM fails, service falls back to a deterministic answer from verified data."""
    service = GeneralHospitalService()

    async def mock_run_error(*args, **kwargs):
        raise RuntimeError("Groq LLM connection timeout")
        yield  # unreachable

    with patch.object(service.runner, "run_async", side_effect=mock_run_error):
        res = await service.answer_question("What are the hospital's visiting hours?")
        assert res.status == "success"
        # Deterministic fallback should include verified visiting hours
        assert "16:00" in res.answer or "Visiting" in res.answer
        assert len(res.sources) > 0


@pytest.mark.asyncio
async def test_service_unverified_topic_reports_honestly():
    """Questions on unverified topics return an honest message stating data was unverified."""
    service = GeneralHospitalService()

    async def mock_run_error(*args, **kwargs):
        raise RuntimeError("Service unavailable")
        yield

    with patch.object(service.runner, "run_async", side_effect=mock_run_error):
        res = await service.answer_question("Can I pay with Bitcoin or dogecoin?")
        assert res.status == "success"
        assert "could not verify" in res.answer.lower()

