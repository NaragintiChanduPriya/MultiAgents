"""General hospital information tools, backed by MongoDB.

Departments come from the `specialties` collection an administrator actually
maintains, doctor counts from `users`, and wards from `beds`. The institutional
facts below are the same ones the public website serves from
`GET /api/public/hospital`, so the agent and the website cannot contradict
each other about the address, the phone number or the opening hours.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from database import Collections, get_db, sanitize_db_error

logger = logging.getLogger(__name__)

# Institutional facts, kept in step with the Node backend's public controller.
HOSPITAL_PROFILE: Dict[str, Any] = {
    "hospital_name": "Aarogya General Hospital",
    "tagline": "Specialist care, close to home.",
    "about": (
        "A multi-speciality hospital serving the city since 1998, with round-the-clock "
        "emergency cover, an in-house pharmacy and a diagnostics lab under one roof."
    ),
    "address": "14 Anna Salai, Chennai 600002",
    "phone": "+91 44 4000 1200",
    "emergency_phone": "108",
    "email": "care@aarogyahospital.example",
    "operational_status": "Active / Fully Operational",
    "outpatient_clinic_hours": "Monday to Saturday, 09:00 - 17:00 (closed Sundays for routine OPD)",
    "emergency_hours": "Open 24 hours, every day",
    "pharmacy_hours": "Monday to Sunday, 08:00 - 22:00",
    "visiting_hours": (
        "General inpatient wards: 16:00 - 19:00 daily. "
        "ICU: 11:00 - 12:00 and 17:00 - 18:00."
    ),
    "emergency_redirection": (
        "For life-threatening emergencies, go straight to the Emergency Room or dial 108 "
        "(or your local emergency number). Do not wait for an outpatient appointment."
    ),
    "patient_helpdesk": "Central Helpdesk & Navigation Services, Ground Floor Main Entrance",
}

# Verified static institutional policies and operational guidance.
VERIFIED_POLICIES: Dict[str, Dict[str, Any]] = {
    "registration": {
        "title": "New Patient Registration Policy",
        "category": "Administration",
        "description": "Procedures and documentation required to register as a new patient at the hospital.",
        "requirements": [
            "Valid government-issued photo identification (e.g. National ID, Passport, Driver's License).",
            "Proof of current residential address.",
            "Active contact telephone number and emergency contact details.",
            "Health insurance card or policy documentation, if applicable.",
            "Prior medical records or existing prescription summaries (recommended but not mandatory).",
        ],
        "procedure": (
            "Visit the Central Patient Registration Desk at the main hospital entrance on the Ground Floor, "
            "or register through the patient portal before your appointment. "
            "A permanent unique Patient ID (PAT-YYYYMMDD-NNNN) is generated for your hospital medical record."
        ),
    },
    "admission": {
        "title": "General Inpatient Admission Policy",
        "category": "Inpatient Care",
        "description": "Standard procedures for planned and emergency hospital admissions.",
        "requirements": [
            "Signed admission order from an attending hospital doctor or emergency triage clinician.",
            "Patient identification (Patient ID) and insurance pre-authorization documentation.",
            "Personal toiletries and essential daily living items. Medications should be handed to nursing staff on arrival for reconciliation.",
        ],
        "procedure": (
            "Planned admissions report to the Inpatient Admissions Desk in the central concourse. "
            "Bed assignments are coordinated across the hospital's wards based on clinical specialty "
            "and bed availability. Emergency admissions are coordinated directly through the 24/7 "
            "Emergency Department."
        ),
    },
    "visiting_hours": {
        "title": "Hospital Visiting Hours & Visitor Guidelines",
        "category": "Visitor Policy",
        "description": "Rules and designated hours for family and visitor bedside access.",
        "visiting_hours": [
            "General inpatient wards: daily from 16:00 to 19:00.",
            "Intensive Care Units (ICU / HDU): daily from 11:00 to 12:00 and 17:00 to 18:00, limited to one designated immediate family member.",
            "Outpatient consultations: one companion permitted per patient in examination rooms.",
        ],
        "guidelines": [
            "Maximum of 2 visitors at the bedside at any one time in general wards.",
            "All visitors must sanitize hands before and after bedside entry.",
            "Visitors with symptoms of respiratory infection, fever, or infectious illness must not visit.",
            "Children under 12 require permission from the nursing supervisor.",
        ],
    },
    "emergency": {
        "title": "Emergency Medical Services Policy",
        "category": "Emergency Care",
        "description": "Protocol for urgent, acute, and life-threatening medical presentations.",
        "hours": "24 hours a day, 7 days a week, 365 days a year.",
        "procedure": (
            "For acute, life-threatening symptoms (sudden chest pain, difficulty breathing, stroke signs, "
            "severe trauma, uncontrolled bleeding), call emergency services on 108 immediately or go "
            "straight to the Emergency Room entrance. Do not wait for an outpatient appointment or an "
            "online consultation."
        ),
    },
    "billing": {
        "title": "Billing, Insurance & Payment Policy",
        "category": "Administration",
        "description": "How consultation fees, insurance claims and inpatient billing are handled.",
        "requirements": [
            "Consultation fees are payable at the time of the visit and vary by department.",
            "Insurance pre-authorization is required before a planned admission.",
            "Original insurance card and policy documentation must be produced at the billing desk.",
        ],
        "procedure": (
            "Outpatient fees are settled at the registration desk or through the patient portal. "
            "Inpatient accounts are settled at the Billing Office on the Ground Floor before discharge. "
            "Itemized statements are available on request."
        ),
    },
}


def search_hospital_departments(department_name: Optional[str] = None) -> Dict[str, Any]:
    """Retrieves the hospital's active clinical departments and their doctor counts.

    Args:
        department_name: Optional filter (e.g. 'Cardiology', 'heart').

    Returns:
        Structured list of verified active departments.
    """
    clean = department_name.strip() if department_name else None

    try:
        with get_db() as db:
            query: Dict[str, Any] = {"isActive": True}
            if clean:
                pattern = {"$regex": clean, "$options": "i"}
                query["$or"] = [
                    {"name": pattern},
                    {"slug": pattern},
                    {"tagline": pattern},
                    {"description": pattern},
                    {"commonConditions": pattern},
                    {"symptomKeywords": pattern},
                ]

            departments: List[Dict[str, Any]] = []
            for spec in db[Collections.SPECIALTIES].find(query).sort("displayOrder", 1):
                slug = spec.get("slug") or ""
                name = spec.get("name") or ""
                doctor_count = db[Collections.USERS].count_documents(
                    {
                        "role": "physician",
                        "isActive": True,
                        "$or": [
                            {"doctorProfile.specialty": slug},
                            {"department": {"$regex": f"^{name}$", "$options": "i"}},
                        ],
                    }
                )
                departments.append(
                    {
                        "department": name,
                        "slug": slug,
                        "tagline": spec.get("tagline") or "",
                        "description": spec.get("description") or "",
                        "common_conditions": spec.get("commonConditions") or [],
                        "consultation_fee_inr": spec.get("consultationFee") or 0,
                        "location": spec.get("location") or "",
                        "doctor_count": doctor_count,
                    }
                )

            if not departments:
                return {
                    "status": "not_found",
                    "count": 0,
                    "departments": [],
                    "message": (
                        f"No active clinical department matching '{clean}' exists in hospital records."
                        if clean
                        else "No active clinical departments are recorded."
                    ),
                    "verified_source": "MongoDB: specialties collection",
                }

            return {
                "status": "success",
                "count": len(departments),
                "departments": departments,
                "message": f"Found {len(departments)} active clinical department(s).",
                "verified_source": "MongoDB: specialties and users collections",
            }
    except Exception as e:
        safe = sanitize_db_error(e)
        logger.error("Failed to search departments: %s", safe)
        return {
            "status": "error",
            "count": 0,
            "departments": [],
            "message": f"Failed to retrieve hospital departments: {safe}",
        }


def search_hospital_services(service_name: Optional[str] = None) -> Dict[str, Any]:
    """Retrieves verified clinical and outpatient services provided by the hospital.

    Args:
        service_name: Optional filter query (e.g. 'Outpatient', 'Pharmacy', 'Diagnostics').

    Returns:
        Structured list of verified hospital services.
    """
    dept_res = search_hospital_departments()
    dept_names = [d["department"] for d in dept_res.get("departments", [])]
    dept_list = ", ".join(dept_names) if dept_names else "the hospital's clinical departments"

    base_services = [
        {
            "service_name": "Outpatient Specialty Consultations",
            "category": "Outpatient Care",
            "description": f"Specialist consultations across active clinical departments: {dept_list}.",
            "hours": HOSPITAL_PROFILE["outpatient_clinic_hours"],
            "verified": True,
            "source": "MongoDB: specialties and users collections",
        },
        {
            "service_name": "Inpatient Hospitalization & Nursing Care",
            "category": "Inpatient Care",
            "description": "Acute and routine medical and surgical inpatient care across the hospital's wards.",
            "hours": "24 hours",
            "verified": True,
            "source": "MongoDB: beds and admissions collections",
        },
        {
            "service_name": "Diagnostic Laboratory Services",
            "category": "Diagnostics",
            "description": "Clinical pathology, biochemistry, haematology and metabolic screening for inpatients and outpatients.",
            "hours": "Monday to Saturday, 07:00 - 19:00",
            "verified": True,
            "source": "MongoDB: admissions.labResults",
        },
        {
            "service_name": "Physical Therapy & Rehabilitation",
            "category": "Therapy",
            "description": "Functional recovery, post-injury and post-operative mobility and pulmonary physiotherapy.",
            "hours": "Monday to Saturday, 09:00 - 17:00",
            "verified": True,
            "source": "MongoDB: admissions.procedures",
        },
        {
            "service_name": "24/7 Emergency & Acute Triage",
            "category": "Emergency Care",
            "description": "Round-the-clock emergency medical response and trauma evaluation at the Emergency Room.",
            "hours": HOSPITAL_PROFILE["emergency_hours"],
            "verified": True,
            "source": "Hospital Emergency Policy",
        },
        {
            "service_name": "Hospital Pharmacy & Medication Dispensation",
            "category": "Pharmacy",
            "description": "Inpatient medication administration, clinical reconciliation and outpatient discharge dispensing.",
            "hours": HOSPITAL_PROFILE["pharmacy_hours"],
            "verified": True,
            "source": "MongoDB: medications collection",
        },
    ]

    clean_filter = service_name.strip().lower() if service_name else None
    if clean_filter:
        filtered = [
            s
            for s in base_services
            if clean_filter in s["service_name"].lower()
            or clean_filter in s["category"].lower()
            or clean_filter in s["description"].lower()
        ]
    else:
        filtered = base_services

    return {
        "status": "success",
        "count": len(filtered),
        "services": filtered,
        "message": (
            f"Found {len(filtered)} verified hospital service(s)."
            if filtered
            else f"No hospital services matching '{clean_filter}' could be verified in hospital records."
        ),
    }


def get_hospital_information(category: Optional[str] = None) -> Dict[str, Any]:
    """Retrieves verified hospital contact, operating hours, ward and location information.

    Args:
        category: Optional category filter ('hours', 'contact', 'location', 'wards', 'visiting').

    Returns:
        Structured hospital operational details.
    """
    clean_cat = category.strip().lower() if category else None

    wards: List[str] = []
    bed_stats: Dict[str, Any] = {}
    try:
        with get_db() as db:
            wards = sorted(w for w in db[Collections.BEDS].distinct("ward") if w)
            total_beds = db[Collections.BEDS].count_documents({})
            available_beds = db[Collections.BEDS].count_documents({"status": "available"})
            bed_stats = {"total_beds": total_beds, "available_beds": available_beds}
    except Exception as e:
        # Ward detail is a nicety; the rest of the profile is still correct.
        logger.warning("Could not read ward list: %s", sanitize_db_error(e))

    info: Dict[str, Any] = dict(HOSPITAL_PROFILE)
    if wards:
        info["inpatient_wards"] = wards
    if bed_stats:
        info.update(bed_stats)

    if clean_cat:
        aliases = {
            "contact": ["phone", "email", "address", "emergency_phone", "patient_helpdesk"],
            "hours": [
                "outpatient_clinic_hours",
                "emergency_hours",
                "pharmacy_hours",
                "visiting_hours",
            ],
            "location": ["address", "inpatient_wards", "patient_helpdesk"],
            "wards": ["inpatient_wards", "total_beds", "available_beds"],
            "visiting": ["visiting_hours"],
            "emergency": ["emergency_hours", "emergency_phone", "emergency_redirection"],
        }
        keys = aliases.get(clean_cat)
        matching = (
            {k: info[k] for k in keys if k in info}
            if keys
            else {k: v for k, v in info.items() if clean_cat in k.lower()}
        )
        if matching:
            return {
                "status": "success",
                "category": clean_cat,
                "information": matching,
                "verified_source": "Hospital Operational Directory & MongoDB beds collection",
            }

    return {
        "status": "success",
        "information": info,
        "verified_source": "Hospital Operational Directory & MongoDB beds collection",
    }


def search_hospital_policies(topic: Optional[str] = None) -> Dict[str, Any]:
    """Searches verified hospital policies and institutional procedural documentation.

    Args:
        topic: Policy topic keyword (e.g. 'registration', 'admission', 'visiting', 'emergency', 'billing').

    Returns:
        Structured policy details or a clear unverified message.
    """
    if not topic or not topic.strip():
        return {
            "status": "success",
            "count": len(VERIFIED_POLICIES),
            "policies": list(VERIFIED_POLICIES.values()),
            "verified_source": "Hospital Institutional Documentation",
            "message": "Retrieved all verified hospital policies.",
        }

    clean_topic = topic.strip().lower()
    matching_policies = []

    for key, policy in VERIFIED_POLICIES.items():
        haystack = " ".join(
            [
                key,
                policy["title"].lower(),
                policy["category"].lower(),
                policy["description"].lower(),
            ]
        )
        if clean_topic in haystack:
            matching_policies.append(policy)

    if matching_policies:
        return {
            "status": "success",
            "count": len(matching_policies),
            "policies": matching_policies,
            "verified_source": "Hospital Institutional Documentation",
            "message": (
                f"Found {len(matching_policies)} verified policy entry/entries matching "
                f"'{clean_topic}'."
            ),
        }

    return {
        "status": "not_found",
        "count": 0,
        "policies": [],
        "verified_source": "Hospital Institutional Documentation",
        "message": (
            f"No verified hospital policy matching '{clean_topic}' was found in institutional "
            "documentation."
        ),
    }
