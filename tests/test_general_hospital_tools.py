"""General hospital information tool tests, run against an in-memory MongoDB."""

from __future__ import annotations

from agents.tools.general_hospital_tools import (
    get_hospital_information,
    search_hospital_departments,
    search_hospital_policies,
    search_hospital_services,
)


# ══════════════════════════════════════════════════════════════════════════════
# 1. Departments
# ══════════════════════════════════════════════════════════════════════════════


def test_search_departments_returns_active_departments(fake_db):
    """Active departments come back with their doctor counts."""
    result = search_hospital_departments()
    assert result["status"] == "success"
    assert result["count"] == 2

    names = [d["department"] for d in result["departments"]]
    assert "Cardiology" in names
    assert "General Medicine" in names


def test_search_departments_hides_inactive_departments(fake_db):
    """A department an administrator switched off is not shown to patients."""
    names = [d["department"] for d in search_hospital_departments()["departments"]]
    assert "Retired Unit" not in names


def test_search_departments_counts_only_active_doctors(fake_db):
    """The doctor count excludes doctors who are no longer active."""
    result = search_hospital_departments("Cardiology")
    cardiology = result["departments"][0]
    assert cardiology["department"] == "Cardiology"
    assert cardiology["doctor_count"] == 2  # the inactive third is excluded


def test_search_departments_carries_the_public_detail(fake_db):
    """The blurb, fee and location the website shows travel with the department."""
    cardiology = search_hospital_departments("Cardiology")["departments"][0]
    assert cardiology["tagline"] == "Heart and rhythm care."
    assert cardiology["consultation_fee_inr"] == 800
    assert cardiology["location"] == "Second floor, Block B"
    assert "Heart failure" in cardiology["common_conditions"]


def test_search_departments_matches_a_symptom(fake_db):
    """A described symptom finds the department that treats it."""
    result = search_hospital_departments("palpitations")
    assert result["status"] == "success"
    assert result["departments"][0]["department"] == "Cardiology"


def test_search_departments_sorts_by_display_order(fake_db):
    """Departments appear in the order an administrator set."""
    result = search_hospital_departments()
    assert result["departments"][0]["department"] == "General Medicine"


def test_search_departments_unknown_department(fake_db):
    """A department that does not exist is reported, not invented."""
    result = search_hospital_departments("Aerospace Surgery")
    assert result["status"] == "not_found"
    assert result["count"] == 0
    assert "Aerospace Surgery" in result["message"]


def test_search_departments_reports_a_database_failure(fake_db, monkeypatch):
    """A driver failure returns a sanitized error rather than a stack trace."""
    import database as database_module

    def explode(*_args, **_kwargs):
        raise RuntimeError("connection timed out")

    monkeypatch.setattr(database_module, "get_database", explode)
    result = search_hospital_departments("Cardiology")
    assert result["status"] == "error"
    assert result["count"] == 0
    assert "connection timed out" in result["message"]


# ══════════════════════════════════════════════════════════════════════════════
# 2. Services
# ══════════════════════════════════════════════════════════════════════════════


def test_search_services_lists_every_service(fake_db):
    """All verified hospital services come back by default."""
    result = search_hospital_services()
    assert result["status"] == "success"
    assert result["count"] == 6

    names = [s["service_name"] for s in result["services"]]
    assert any("Outpatient" in n for n in names)
    assert any("Emergency" in n for n in names)
    assert any("Pharmacy" in n for n in names)


def test_search_services_names_the_live_departments(fake_db):
    """The outpatient blurb lists the departments that actually exist."""
    outpatient = search_hospital_services("Outpatient")["services"][0]
    assert "Cardiology" in outpatient["description"]
    assert "General Medicine" in outpatient["description"]


def test_search_services_filters(fake_db):
    """A filter narrows the list to matching services."""
    result = search_hospital_services("Pharmacy")
    assert result["status"] == "success"
    assert all("Pharmacy" in s["service_name"] or "Pharmacy" in s["category"] for s in result["services"])


def test_search_services_unknown_service(fake_db):
    """A service the hospital does not offer is reported as unverified."""
    result = search_hospital_services("Aerospace Surgery")
    assert result["count"] == 0
    assert "No hospital services matching" in result["message"]


# ══════════════════════════════════════════════════════════════════════════════
# 3. Hospital information
# ══════════════════════════════════════════════════════════════════════════════


def test_get_hospital_information_returns_the_profile(fake_db):
    """Contact details, hours and visiting rules all come back."""
    result = get_hospital_information()
    assert result["status"] == "success"

    info = result["information"]
    assert info["hospital_name"] == "Aarogya General Hospital"
    assert info["phone"] == "+91 44 4000 1200"
    assert info["emergency_phone"] == "108"
    assert "24 hours" in info["emergency_hours"].lower()
    assert "16:00 - 19:00" in info["visiting_hours"]


def test_get_hospital_information_reads_wards_from_the_database(fake_db):
    """The ward list is the real one, not a hard-coded guess."""
    info = get_hospital_information()["information"]
    assert info["inpatient_wards"] == ["Cardiology 3B", "General Medicine 2A"]
    assert info["total_beds"] == 2
    assert info["available_beds"] == 1


def test_get_hospital_information_by_category(fake_db):
    """A category filter returns just the relevant keys."""
    visiting = get_hospital_information("visiting")
    assert visiting["status"] == "success"
    assert "visiting_hours" in visiting["information"]

    contact = get_hospital_information("contact")
    assert "phone" in contact["information"]
    assert "address" in contact["information"]

    wards = get_hospital_information("wards")
    assert "inpatient_wards" in wards["information"]


def test_get_hospital_information_survives_a_database_failure(fake_db, monkeypatch):
    """Ward detail is a nicety: the rest of the profile still answers."""
    import database as database_module

    def explode(*_args, **_kwargs):
        raise RuntimeError("connection timed out")

    monkeypatch.setattr(database_module, "get_database", explode)
    result = get_hospital_information()
    assert result["status"] == "success"
    assert result["information"]["hospital_name"] == "Aarogya General Hospital"


# ══════════════════════════════════════════════════════════════════════════════
# 4. Policies
# ══════════════════════════════════════════════════════════════════════════════


def test_search_policies_returns_everything_by_default():
    """With no topic, every verified policy is returned."""
    result = search_hospital_policies()
    assert result["status"] == "success"
    assert result["count"] == 5


def test_search_policies_registration():
    """The registration policy carries its documentation requirements."""
    result = search_hospital_policies("registration")
    assert result["status"] == "success"
    assert "New Patient Registration Policy" in result["policies"][0]["title"]
    assert len(result["policies"][0]["requirements"]) > 0


def test_search_policies_visiting():
    """The visiting policy carries hours and visitor guidelines."""
    result = search_hospital_policies("visiting")
    assert result["status"] == "success"
    assert "Visitor" in result["policies"][0]["title"]
    assert len(result["policies"][0]["guidelines"]) > 0


def test_search_policies_emergency_directs_to_emergency_care():
    """The emergency policy names the number to call.

    'emergency' legitimately matches the admission policy too, since emergency
    admissions are described there, so assert across the matches rather than
    assuming an order.
    """
    result = search_hospital_policies("emergency")
    assert result["status"] == "success"
    titles = [p["title"] for p in result["policies"]]
    assert "Emergency Medical Services Policy" in titles

    emergency = next(p for p in result["policies"] if p["title"].startswith("Emergency"))
    assert "108" in emergency["procedure"]
    assert "24 hours a day" in emergency["hours"]


def test_search_policies_billing():
    """Billing and insurance are covered."""
    result = search_hospital_policies("billing")
    assert result["status"] == "success"
    assert "Billing" in result["policies"][0]["title"]


def test_search_policies_unknown_topic():
    """A topic with no verified policy says so rather than improvising one."""
    result = search_hospital_policies("crypto payments")
    assert result["status"] == "not_found"
    assert result["count"] == 0
    assert "No verified hospital policy" in result["message"]
