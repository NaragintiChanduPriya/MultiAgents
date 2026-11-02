"""Shared test fixtures.

The tool tests run against an in-memory MongoDB (mongomock) rather than mocked
cursors, so they exercise the real query, projection and sort logic instead of
asserting that a mock was called. `database.get_database` is the single seam:
`get_db()` resolves it at call time, so patching it redirects every tool.
"""

from __future__ import annotations

import datetime
from typing import Any, Dict

import mongomock
import pytest
from bson import ObjectId

import database as database_module

# ─── Stable identifiers, so tests can reference records by name ──────────────

DOCTOR_ID = ObjectId("aaaaaaaaaaaaaaaaaaaaaaa1")
DOCTOR_2_ID = ObjectId("aaaaaaaaaaaaaaaaaaaaaaa2")
INACTIVE_DOCTOR_ID = ObjectId("aaaaaaaaaaaaaaaaaaaaaaa3")
PATIENT_ID = ObjectId("bbbbbbbbbbbbbbbbbbbbbbb1")
PATIENT_2_ID = ObjectId("bbbbbbbbbbbbbbbbbbbbbbb2")
ADMISSION_ID = ObjectId("ccccccccccccccccccccccc1")
PACKAGE_ID = ObjectId("ddddddddddddddddddddddd1")
MED_PRE_DOC_ID = ObjectId("eeeeeeeeeeeeeeeeeeeeeee1")
MED_DISCHARGE_DOC_ID = ObjectId("eeeeeeeeeeeeeeeeeeeeeee2")
MED_ITEM_LISINOPRIL = ObjectId("fff000000000000000000001")
MED_ITEM_WARFARIN = ObjectId("fff000000000000000000002")
MED_ITEM_ASPIRIN = ObjectId("fff000000000000000000003")
RX_ID = ObjectId("aaa111111111111111111111")
RX_ITEM_AMOXICILLIN = ObjectId("aaa222222222222222222221")
RX_ITEM_IBUPROFEN = ObjectId("aaa222222222222222222222")

PATIENT_HUMAN_ID = "PAT-20260101-0001"
PATIENT_2_HUMAN_ID = "PAT-20260101-0002"
ADMISSION_HUMAN_ID = "ADM-20260101-0001"
PACKAGE_HUMAN_ID = "DIS-20260101-0001"
APPOINTMENT_HUMAN_ID = "APT-20260101-0001"
PRESCRIPTION_HUMAN_ID = "RX-20260101-0001"


def future_clinic_date() -> datetime.datetime:
    """A date far enough ahead that the 'slot is in the past' guard never fires."""
    base = datetime.datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    return base + datetime.timedelta(days=7)


def seed_data() -> Dict[str, Any]:
    """Builds the document set every tool test shares."""
    now = datetime.datetime.now()
    admitted = now - datetime.timedelta(days=4)

    return {
        "specialties": [
            {
                "_id": ObjectId(),
                "slug": "cardiology",
                "name": "Cardiology",
                "tagline": "Heart and rhythm care.",
                "description": "Heart failure, chest pain and arrhythmia services.",
                "symptomKeywords": ["chest pain", "palpitations", "breathless"],
                "commonConditions": ["Heart failure", "Atrial fibrillation"],
                "consultationFee": 800,
                "location": "Second floor, Block B",
                "isActive": True,
                "displayOrder": 20,
            },
            {
                "_id": ObjectId(),
                "slug": "general-medicine",
                "name": "General Medicine",
                "tagline": "The first stop when you are not sure where to start.",
                "description": "Undifferentiated illness and long-term conditions.",
                "symptomKeywords": ["fever", "fatigue"],
                "commonConditions": ["Diabetes", "Hypertension"],
                "consultationFee": 400,
                "location": "Ground floor, Block A",
                "isActive": True,
                "displayOrder": 10,
            },
            {
                "_id": ObjectId(),
                "slug": "retired-unit",
                "name": "Retired Unit",
                "tagline": "",
                "description": "",
                "symptomKeywords": [],
                "commonConditions": [],
                "consultationFee": 0,
                "location": "",
                "isActive": False,
                "displayOrder": 99,
            },
        ],
        "users": [
            {
                "_id": DOCTOR_ID,
                "email": "dr.sharma@hospital.test",
                "passwordHash": "should-never-be-returned",
                "firstName": "Ananya",
                "lastName": "Sharma",
                "role": "physician",
                "department": "Cardiology",
                "specialization": "Cardiology",
                "isActive": True,
                "doctorProfile": {
                    "specialty": "cardiology",
                    "qualifications": "MBBS, MD, DM (Cardiology)",
                    "yearsExperience": 18,
                    "bio": "Interventional cardiologist.",
                    "languages": ["English", "Tamil"],
                    "consultationFee": 800,
                    "availableDays": [0, 1, 2, 3, 4, 5, 6],
                    "clinicStart": "09:00",
                    "clinicEnd": "11:00",
                    "slotMinutes": 30,
                    "bufferMinutes": 0,
                    "isBookable": True,
                },
            },
            {
                "_id": DOCTOR_2_ID,
                "email": "dr.rao@hospital.test",
                "passwordHash": "should-never-be-returned",
                "firstName": "Vikram",
                "lastName": "Rao",
                "role": "physician",
                "department": "Cardiology",
                "specialization": "Cardiology",
                "isActive": True,
                "doctorProfile": {
                    "specialty": "cardiology",
                    "yearsExperience": 9,
                    "consultationFee": 600,
                    "availableDays": [0, 1, 2, 3, 4, 5, 6],
                    "clinicStart": "09:00",
                    "clinicEnd": "10:00",
                    "slotMinutes": 30,
                    "bufferMinutes": 0,
                    "isBookable": True,
                },
            },
            {
                "_id": INACTIVE_DOCTOR_ID,
                "email": "dr.retired@hospital.test",
                "passwordHash": "should-never-be-returned",
                "firstName": "Priya",
                "lastName": "Nair",
                "role": "physician",
                "department": "Cardiology",
                "specialization": "Cardiology",
                "isActive": False,
                "doctorProfile": {
                    "specialty": "cardiology",
                    "yearsExperience": 25,
                    "consultationFee": 900,
                    "availableDays": [0, 1, 2, 3, 4, 5, 6],
                    "clinicStart": "09:00",
                    "clinicEnd": "10:00",
                    "slotMinutes": 30,
                    "bufferMinutes": 0,
                    "isBookable": False,
                },
            },
        ],
        "patients": [
            {
                "_id": PATIENT_ID,
                "patientId": PATIENT_HUMAN_ID,
                "demographics": {
                    "firstName": "Senthil",
                    "lastName": "Kumar",
                    "dateOfBirth": datetime.datetime(1958, 7, 15),
                    "gender": "male",
                    "bloodType": "AB-",
                    "preferredLanguage": "English",
                    "literacyLevel": "basic",
                    "phone": "555-0236",
                },
                "medicalHistory": {
                    "conditions": ["Chronic heart failure (HFrEF)", "Atrial fibrillation"],
                    "allergies": [
                        {
                            "substance": "Penicillin",
                            "reaction": "Rash",
                            "severity": "moderate",
                        }
                    ],
                    "surgeries": [],
                    "familyHistory": [],
                },
                "status": "discharge_pending",
            },
            {
                "_id": PATIENT_2_ID,
                "patientId": PATIENT_2_HUMAN_ID,
                "demographics": {
                    "firstName": "Lakshmi",
                    "lastName": "Iyer",
                    "dateOfBirth": datetime.datetime(1972, 3, 2),
                    "gender": "female",
                    "bloodType": "O+",
                    "preferredLanguage": "Tamil",
                    "literacyLevel": "intermediate",
                    "phone": "555-0400",
                },
                "medicalHistory": {
                    "conditions": [],
                    "allergies": [],
                    "surgeries": [],
                    "familyHistory": [],
                },
                "status": "registered",
            },
        ],
        "admissions": [
            {
                "_id": ADMISSION_ID,
                "admissionId": ADMISSION_HUMAN_ID,
                "patientId": PATIENT_ID,
                "admittingDiagnosis": "Acute decompensated heart failure",
                "diagnosisCodes": ["I50.23", "I48.91"],
                "admissionDate": admitted,
                "expectedDischargeDate": now + datetime.timedelta(days=1),
                "actualDischargeDate": None,
                "bed": {"ward": "Cardiology 3B", "roomNumber": "301", "bedNumber": "A"},
                "riskAssessment": {
                    "fallRisk": "moderate",
                    "pressureInjuryRisk": "low",
                    "infectionFlags": [],
                },
                "status": "active",
                "clinicalNotes": [
                    {
                        "_id": ObjectId(),
                        "type": "assessment",
                        "content": "Bibasal crackles, raised JVP, in atrial fibrillation at 112 bpm.",
                        "createdAt": admitted,
                    }
                ],
                "labResults": [
                    {
                        "_id": ObjectId(),
                        "testName": "NT-proBNP",
                        "value": "4210",
                        "unit": "pg/mL",
                        "referenceRange": "<125",
                        "isAbnormal": True,
                        "resultDate": admitted,
                    },
                    {
                        "_id": ObjectId(),
                        "testName": "Potassium",
                        "value": "3.9",
                        "unit": "mmol/L",
                        "referenceRange": "3.5-5.1",
                        "isAbnormal": False,
                        "resultDate": admitted,
                    },
                ],
                "procedures": [
                    {
                        "_id": ObjectId(),
                        "name": "Transthoracic echocardiogram",
                        "date": admitted,
                        "notes": "LVEF 32%",
                    }
                ],
            }
        ],
        "medications": [
            {
                "_id": MED_PRE_DOC_ID,
                "patientId": PATIENT_ID,
                "admissionId": ADMISSION_ID,
                "listType": "pre_admission",
                "medications": [
                    {
                        "_id": MED_ITEM_LISINOPRIL,
                        "drugName": "Lisinopril",
                        "genericName": "lisinopril",
                        "drugClass": "ACE inhibitor",
                        "dosage": "10 mg",
                        "frequency": "once daily",
                        "route": "oral",
                        "isActive": True,
                        "notes": None,
                        "startDate": admitted,
                        "endDate": None,
                    },
                    {
                        "_id": MED_ITEM_WARFARIN,
                        "drugName": "Warfarin",
                        "genericName": "warfarin",
                        "drugClass": "Anticoagulant",
                        "dosage": "5 mg",
                        "frequency": "once daily in the evening",
                        "route": "oral",
                        "isActive": True,
                        "notes": "Target INR 2.0-3.0 for atrial fibrillation",
                        "startDate": admitted,
                        "endDate": None,
                    },
                ],
            },
            {
                "_id": MED_DISCHARGE_DOC_ID,
                "patientId": PATIENT_ID,
                "admissionId": ADMISSION_ID,
                "listType": "discharge",
                "medications": [
                    {
                        "_id": MED_ITEM_ASPIRIN,
                        "drugName": "Aspirin",
                        "genericName": "aspirin",
                        "drugClass": "Antiplatelet",
                        "dosage": "81 mg",
                        "frequency": "once daily",
                        "route": "oral",
                        "isActive": True,
                        "notes": None,
                        "startDate": admitted,
                        "endDate": None,
                    }
                ],
            },
        ],
        "dischargepackages": [
            {
                "_id": PACKAGE_ID,
                "packageId": PACKAGE_HUMAN_ID,
                "patientId": PATIENT_ID,
                "admissionId": ADMISSION_ID,
                "status": "pending_review",
                "initiatedAt": now - datetime.timedelta(hours=2),
                "completedAt": None,
                "clinicalSummary": {
                    "status": "generated",
                    "content": "Admitted with acute decompensated heart failure; diuresed to dry weight.",
                    "generatedAt": now - datetime.timedelta(hours=1),
                    "agentConfidence": 0.88,
                    "generator": "claude",
                },
                "medicationReconciliation": {
                    "status": "generated",
                    "flags": [
                        {
                            "flagId": "F1",
                            "type": "interaction",
                            "severity": "critical",
                            "drugA": "Warfarin",
                            "drugB": "Aspirin",
                            "description": "Bleeding risk.",
                            "recommendation": "Confirm intent.",
                            "confidence": 0.9,
                            "resolution": "pending",
                        }
                    ],
                    "agentConfidence": 0.82,
                },
                "followUpPlan": {
                    "status": "generated",
                    "recommendations": [
                        {
                            "type": "pcp_visit",
                            "description": "Heart failure clinic review",
                            "timeframe": "within 7 days",
                            "priority": "required",
                            "specialty": "cardiology",
                            "dueInDays": 7,
                            "isScheduled": False,
                        }
                    ],
                    "agentConfidence": 0.8,
                },
                "patientEducation": {
                    "status": "generated",
                    "content": "Weigh yourself each morning and call if you gain 2 kg in 3 days.",
                    "readingLevel": "basic",
                    "language": "English",
                    "agentConfidence": 0.86,
                    "generator": "claude",
                },
                "safetyValidation": {
                    "passed": True,
                    "checks": [{"name": "allergy_cross_check", "passed": True, "detail": "OK"}],
                    "validatedAt": now,
                },
                "approvals": [
                    {"role": "physician", "section": "clinicalSummary", "action": "approved"}
                ],
                "agentRuns": [],
            }
        ],
        "vitals": [
            {
                "_id": ObjectId(),
                "patientId": PATIENT_ID,
                "admissionId": ADMISSION_ID,
                "timestamp": admitted,
                "bloodPressureSystolic": 139,
                "bloodPressureDiastolic": 91,
                "heartRate": 109,
                "temperature": 36.8,
                "respiratoryRate": 21,
                "oxygenSaturation": 93,
                "painScore": 3,
                "weight": 65.1,
            }
        ],
        "beds": [
            {
                "_id": ObjectId(),
                "ward": "Cardiology 3B",
                "roomNumber": "301",
                "bedNumber": "A",
                "status": "occupied",
            },
            {
                "_id": ObjectId(),
                "ward": "General Medicine 2A",
                "roomNumber": "201",
                "bedNumber": "B",
                "status": "available",
            },
        ],
        "prescriptions": [
            {
                "_id": RX_ID,
                "prescriptionId": PRESCRIPTION_HUMAN_ID,
                "patientId": PATIENT_ID,
                "admissionId": ADMISSION_ID,
                "prescribedBy": DOCTOR_ID,
                "prescribedAt": now - datetime.timedelta(hours=1),
                "status": "ready",
                "priority": "routine",
                "diagnosis": "Lower respiratory tract infection",
                "notes": "Take the full course.",
                "items": [
                    {
                        "_id": RX_ITEM_AMOXICILLIN,
                        "drugName": "Amoxicillin",
                        "genericName": "amoxicillin",
                        "drugClass": "Antibiotic",
                        "dosage": "500 mg",
                        "frequency": "three times a day",
                        "route": "oral",
                        "durationDays": 7,
                        "quantity": 21,
                        "instructions": "Take with food.",
                        "substitutionAllowed": True,
                        "isDispensed": False,
                        "dispensedQuantity": 0,
                        "substitutedWith": None,
                        "pharmacyNote": None,
                    },
                    {
                        "_id": RX_ITEM_IBUPROFEN,
                        "drugName": "Ibuprofen",
                        "genericName": "ibuprofen",
                        "drugClass": "NSAID",
                        "dosage": "400 mg",
                        "frequency": "as needed, up to three times a day",
                        "route": "oral",
                        "durationDays": 0,
                        "quantity": 20,
                        "instructions": "",
                        "substitutionAllowed": True,
                        "isDispensed": False,
                        "dispensedQuantity": 0,
                        "substitutedWith": "Brufen",
                        "pharmacyNote": "Generic supplied.",
                    },
                ],
                "safety": {
                    "checkedAt": now,
                    "passed": False,
                    "flags": [
                        {
                            "flagId": "RXF001",
                            "type": "interaction",
                            "severity": "high",
                            "drugA": "Ibuprofen",
                            "drugB": "Warfarin",
                            "description": "Increased bleeding risk.",
                            "recommendation": "Monitor INR closely.",
                            "acknowledged": False,
                        }
                    ],
                },
                "pharmacy": {
                    "verifiedBy": None,
                    "verifiedAt": None,
                    "readyAt": now,
                    "dispensedBy": None,
                    "dispensedAt": None,
                    "collectedBy": "",
                    "notes": "",
                },
                "counselling": {"content": "", "generatedAt": None, "generator": ""},
                "sentToPharmacyAt": now - datetime.timedelta(minutes=50),
            }
        ],
        "appointments": [
            {
                "_id": ObjectId(),
                "appointmentId": APPOINTMENT_HUMAN_ID,
                "patientId": PATIENT_ID,
                "doctorId": DOCTOR_ID,
                "type": "specialist",
                "specialty": "Cardiology",
                "scheduledDate": future_clinic_date(),
                "scheduledTime": "09:00",
                "duration": 30,
                "status": "scheduled",
                "location": "Clinic 2",
                "reason": "Heart failure review",
            }
        ],
    }


@pytest.fixture
def fake_db(monkeypatch):
    """An in-memory MongoDB seeded with the hospital fixtures, wired into the tools."""
    client = mongomock.MongoClient()
    db = client["test"]

    for collection, documents in seed_data().items():
        if documents:
            db[collection].insert_many(documents)

    monkeypatch.setattr(database_module, "get_database", lambda *a, **k: db)
    return db
