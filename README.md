# Hospital Multi-Agent System — Independent Agents

Four independent hospital AI agents built with **Python 3.12**, **Google ADK**, **Groq LLM**
(`openai/gpt-oss-120b`), **FastAPI**, **MongoDB** and **Pydantic**.

They operate side by side with **no parent agent, no orchestrator and no sub-agents**:

1. **Appointment Agent** — discovers specialties and doctors, computes live slot availability,
   and books appointments with double-booking prevention. Can also list and cancel.
2. **Medication Agent** — retrieves a verified medication record and explains it in
   patient-friendly language.
3. **Discharge Summary Report Agent** — retrieves a verified discharge package and compiles a
   structured summary from the admission, medications, procedures, labs, notes and vitals.
4. **General Hospital Assistant** — answers non-clinical questions (departments, services,
   visiting hours, registration, admission, navigation, contact) with emergency triage.

---

## 1. Architecture

Each agent is a completely independent unit:

- **Zero parent / orchestrator agent.** No meta-agent sits above them.
- **Zero sub-agents.** No agent delegates to a child.
- **MongoDB is the source of truth.** The agents read the *same* database the Node backend
  (`hospital-backend`) writes, so there is one set of records and no second copy to drift.
  Nothing clinical is ever invented by an LLM.
- **Controlled tools only.** Each agent has a fixed set of parameterized query functions.
  There is no free-form query tool, so a prompt-injected "now read the audit log" has nothing
  to call. `inspect_database_schema` is further restricted by a collection allowlist and
  redacts credential fields.
- **Strict authorisation.** Patient data is scoped by verified caller identity
  (`X-Patient-Id` header or Bearer token). A caller whose identity starts with `PAT` is
  treated as that patient and may read only their own records.
- **Emergency triage.** Acute symptoms are detected deterministically — *before* the model is
  called — and the caller is directed to emergency care on **108**.

```
                              +--------------------------------------------------------------+
                              |                        FastAPI Layer                         |
                              |  /appointments  /medications  /discharge-summaries  /hospital|
                              +------------------------------+-------------------------------+
                                                             |
            +------------------------+-----------------------+-----------------------+
            |                        |                       |                       |
            v                        v                       v                       v
+-----------------------+  +-------------------+  +--------------------+  +--------------------+
|  Appointment Service  |  | Medication Service|  |  Discharge Service |  |  General Hospital  |
+-----------+-----------+  +---------+---------+  +----------+---------+  +----------+---------+
            |                        |                       |                       |
            v                        v                       v                       v
+-----------------------+  +-------------------+  +--------------------+  +--------------------+
|   Appointment Agent   |  | Medication Agent  |  |  Discharge Agent   |  | General Hosp Agent |
| (ADK / Groq LLM)      |  | (ADK / Groq LLM)  |  |  (ADK / Groq LLM)  |  | (ADK / Groq LLM)   |
+-----------+-----------+  +---------+---------+  +----------+---------+  +----------+---------+
            |                        |                       |                       |
            +------------------------+-----------+-----------+-----------------------+
                                                 |
                                                 v
                                      MongoDB Hospital Database
           (collections: users, specialties, appointments, patients, admissions,
                         medications, dischargepackages, vitals, beds)
```

### Two things that are not one-to-one with a table

**Doctors** are `users` with `role: 'physician'` and a `doctorProfile`. There is no separate
doctors collection.

**Slots are computed, never stored.** A doctor's clinic days, hours, slot length and buffer
generate the grid; booked appointments knock holes in it. This mirrors
`bookingService.freeSlots` in the Node backend exactly, so the website and the agent always
offer the same times. A slot identifier is a round-trippable token
`<doctorId>_<YYYY-MM-DD>_<HHMM>` — the booking tool re-derives and re-verifies the slot rather
than trusting what it is handed.

### Preventing double-booking without row locks

PostgreSQL would use `SELECT … FOR UPDATE`. MongoDB has no row lock over a computed grid, so
`create_appointment` uses optimistic concurrency: verify the slot is free, insert, then re-read
every active appointment for that doctor/date/time. ObjectIds are monotonic, so each racer
independently agrees the lowest `_id` wins; losers delete their own row and report the clash.
No lock, no lost booking, no dependency on replica-set transactions.

---

## 2. Directory Structure

```text
hospital-multi-agent/
│
├── .env                              # Environment configuration (ignored in git)
├── .env.example                      # Template for environment variables
├── pyproject.toml                    # Project configuration and dependencies
├── run_adk_web.py                    # ADK Web UI launcher script
│
├── main.py                           # FastAPI entrypoint & health check
├── config.py                         # Pydantic settings loader
├── database.py                       # MongoDB connection, serialization, collection names
│
├── agents/
│   ├── groq_compat.py                # Hides Groq chain-of-thought; sanitizes replayed history
│   │
│   ├── appointment_agent/            # Independent Appointment Agent
│   ├── medication_agent/             # Independent Medication Agent
│   ├── discharge_summary_report_agent/
│   ├── general_hospital_agent/
│   │   └── (each: __init__.py, agent.py, <name>.py, instruction.py)
│   │
│   └── tools/
│       ├── common.py                 # Identity resolution + slot computation
│       ├── inspect_schema.py         # Allowlisted collection inspection
│       ├── query_data.py             # Doctors, specialties, computed slots, appointments
│       ├── write_data.py             # Atomic booking and cancellation
│       ├── medication_tools.py       # Medication record lookup and listing
│       ├── discharge_summary_tools.py# Discharge package retrieval
│       └── general_hospital_tools.py # Departments, services, info, policies
│
├── routes/                           # One router per agent
├── schemas/                          # Request/response models
├── services/
│   ├── llm_retry.py                  # Provider rate-limit backoff shared by all agents
│   └── <agent>_service.py            # One coordinator per agent
└── tests/                            # 185 tests (mongomock — no live database needed)
```

---

## 3. Setup

### 1. Environment variables

Copy `.env.example` to `.env`:

```dotenv
GROQ_API_KEY=gsk_your_actual_groq_api_key
GROQ_MODEL=openai/gpt-oss-120b

# The same MongoDB the Node backend uses. A +srv URI with no database path
# lands on "test", which is where the seeded data lives.
MONGODB_URI=mongodb+srv://user:password@cluster.mongodb.net/
MONGODB_DB=test
```

### 2. Install dependencies

```powershell
uv sync
```

Without `uv`, any Python 3.12 virtualenv works:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
```

---

## 4. Running

```powershell
cd hospital-multi-agent
uv run uvicorn main:app --reload --port 8001
```

Port **8001** is what `hospital-backend` expects (`MULTI_AGENT_SERVICE_URL`).

- Swagger UI: http://localhost:8001/docs
- ReDoc: http://localhost:8001/redoc

---

## 5. ADK Dev Web UI

`adk web` scans *subdirectories* of its target for an `agent.py`, so point it at `agents`:

```powershell
cd hospital-multi-agent
uv run adk web agents
```

Or use the launcher:

```powershell
uv run python run_adk_web.py
```

Then open http://localhost:8000/dev-ui/ and pick an app: `appointment_agent`,
`medication_agent`, `discharge_summary_report_agent` or `general_hospital_agent`.

---

## 6. API Endpoints

### 1. Appointment Agent — `POST /api/v1/appointments/chat`
Requires `X-Patient-Id` or a Bearer token.
```json
{ "message": "Are there any Cardiology appointments available tomorrow?", "session_id": null }
```

### 2. Medication Agent — `POST /api/v1/medications/details`
The record id is the embedded medication item's ObjectId.
```json
{ "medication_record_id": "6ae58a2e6d070cf16776db02" }
```

### 3. Discharge Summary Agent — `POST /api/v1/discharge-summaries/generate`
```json
{ "package_id": "DIS-20261031-0001" }
```

### 4. General Hospital Assistant — `POST /api/v1/hospital/ask`
Open to guests; no identity required.
```json
{ "question": "What departments are available in the hospital?" }
```

### 5. Health — `GET /api/v1/health`
Reports service status and live MongoDB connectivity.

---

## 7. Identifier formats

The agents use the same human-readable identifiers the Node backend generates:

| Entity            | Format                  | Example                    |
|-------------------|-------------------------|----------------------------|
| Patient           | `PAT-YYYYMMDD-NNNN`     | `PAT-20261031-0001`        |
| Admission         | `ADM-YYYYMMDD-NNNN`     | `ADM-20261031-0001`        |
| Appointment       | `APT-YYYYMMDD-NNNN`     | `APT-20261101-0001`        |
| Discharge package | `DIS-YYYYMMDD-NNNN`     | `DIS-20261031-0001`        |
| Doctor            | MongoDB ObjectId        | `6ae58a286d070cf16776d9c2` |
| Medication record | Embedded item ObjectId  | `6ae58a2e6d070cf16776db02` |
| Slot              | `<doctorId>_<date>_<HHMM>` | `6ae5…d9c2_2026-11-03_0930` |

Use `list_medications_for_patient` and `list_discharge_packages` to discover the ids an agent
can act on.

---

## 8. Tests

```powershell
uv run pytest -v
```

**185 tests**, all passing, with no live database required — the tool tests run against an
in-memory MongoDB (`mongomock`) seeded with realistic fixtures, so they exercise the real
query, projection and sort logic rather than asserting that a mock was called.

| Area | Files | Covers |
|---|---|---|
| Appointments | `test_appointment_tools.py`, `test_appointment_routes.py` | schema allowlist, doctor search, slot grid, booking guards, double-booking, idempotency, cancellation, authorisation |
| Medications | `test_medication_tools.py`, `test_medication_service.py`, `test_medication_routes.py` | record lookup, strength parsing, listing, authorisation, formatting |
| Discharge | `test_discharge_summary_tools.py`, `test_discharge_summary_service.py`, `test_discharge_summary_routes.py` | package retrieval, embedded clinical records, missing-information reporting |
| General hospital | `test_general_hospital_tools.py`, `test_general_hospital_service.py`, `test_general_hospital_routes.py` | departments, services, policies, emergency triage |
| Rate limiting | `test_llm_retry.py` | throttle detection, backoff, retry, give-up behaviour |

---

## 9. Operational notes

**Groq free-tier rate limits.** The free tier allows 8,000 tokens per minute. A single
appointment-agent turn carries ten tool schemas and costs roughly 4,000 tokens, so two turns
can exhaust the window. `services/llm_retry.py` reads the provider's "try again in Ns" hint,
waits and retries; if it still fails, the caller is told how long to wait rather than shown a
generic error. For sustained use, upgrade the Groq tier or switch `GROQ_MODEL` to a smaller
model.

**Reasoning models.** `openai/gpt-oss-120b` returns chain-of-thought in the same field as the
answer, which would show a patient the model's internal deliberation.
`agents/groq_compat.py` sets `reasoning_format: "hidden"` so Groq drops it server-side, and
strips `reasoning_content` from replayed history (which Groq otherwise rejects with a 400).
