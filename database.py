"""MongoDB access layer for the independent hospital agents.

The agents read and write the same MongoDB the Node backend owns, so there is
exactly one source of truth for patients, admissions, medications, discharge
packages and appointments. Nothing here is fabricated by an LLM: every tool in
`agents/tools` goes through these helpers.

The client is created lazily and reused, because a MongoClient owns a
connection pool and building one per query would be both slow and a socket
leak. It is also fork-safe enough for the single-process uvicorn worker this
service runs under.
"""

from __future__ import annotations

import contextlib
import datetime
import logging
import threading
from decimal import Decimal
from typing import Any, Dict, Generator, Optional
from urllib.parse import urlparse

from bson import ObjectId
from bson.errors import InvalidId
from pymongo import MongoClient
from pymongo.database import Database
from pymongo.errors import PyMongoError

from config import settings

logger = logging.getLogger(__name__)

# Module-level client cache. Guarded by a lock so two concurrent requests on a
# cold start cannot each build their own pool.
_client: Optional[MongoClient] = None
_client_uri: Optional[str] = None
_lock = threading.Lock()


# ─── Error handling ──────────────────────────────────────────────────────────


def sanitize_db_error(error: Exception) -> str:
    """Sanitizes a database exception message so credentials are never exposed.

    A Mongo connection error happily prints the whole URI, password included,
    and that message travels all the way to an API response. Strip it.
    """
    msg = str(error)
    uri = settings.mongodb_uri or ""
    if uri:
        try:
            parsed = urlparse(uri)
            if parsed.password and parsed.password in msg:
                msg = msg.replace(parsed.password, "******")
            if parsed.username and parsed.username in msg:
                msg = msg.replace(parsed.username, "***")
        except Exception:
            pass
    return msg


# ─── Connection management ───────────────────────────────────────────────────


def _resolve_uri(uri: Optional[str] = None) -> str:
    """Resolves the Mongo URI, rewriting localhost to 127.0.0.1 on Windows.

    libmongoc resolves "localhost" to ::1 first on Windows, which stalls for
    seconds before falling back to IPv4 when mongod only listens on v4.
    """
    raw = (uri or settings.mongodb_uri or "").strip()
    if not raw:
        raise ConnectionError(
            "MONGODB_URI is not configured. Please set MONGODB_URI in your .env file."
        )
    # Only rewrite plain mongodb:// — a +srv URI must keep its hostname for the
    # DNS SRV lookup to work at all.
    if raw.startswith("mongodb://"):
        if "@localhost:" in raw:
            return raw.replace("@localhost:", "@127.0.0.1:")
        if raw.startswith("mongodb://localhost:"):
            return raw.replace("mongodb://localhost:", "mongodb://127.0.0.1:", 1)
    return raw


def get_client(uri: Optional[str] = None) -> MongoClient:
    """Returns the shared MongoClient, creating it on first use."""
    global _client, _client_uri
    resolved = _resolve_uri(uri)

    with _lock:
        if _client is not None and _client_uri == resolved:
            return _client
        if _client is not None:
            # The URI changed (tests, or a reconfigure): drop the old pool.
            with contextlib.suppress(Exception):
                _client.close()
        _client = MongoClient(
            resolved,
            serverSelectionTimeoutMS=settings.mongodb_timeout_ms,
            connectTimeoutMS=settings.mongodb_timeout_ms,
            tz_aware=False,
        )
        _client_uri = resolved
        return _client


def get_database(uri: Optional[str] = None, db_name: Optional[str] = None) -> Database:
    """Returns the hospital database handle."""
    client = get_client(uri)
    return client[(db_name or settings.effective_mongodb_db)]


def close_client() -> None:
    """Closes the shared client. Used by tests and on shutdown."""
    global _client, _client_uri
    with _lock:
        if _client is not None:
            with contextlib.suppress(Exception):
                _client.close()
        _client = None
        _client_uri = None


@contextlib.contextmanager
def get_db(
    uri: Optional[str] = None, db_name: Optional[str] = None
) -> Generator[Database, None, None]:
    """Provides the hospital database, translating driver errors into RuntimeError.

    Mirrors the shape the tools already expect: `with get_db() as db: ...`.
    """
    try:
        yield get_database(uri=uri, db_name=db_name)
    except PyMongoError as e:
        safe_msg = sanitize_db_error(e)
        logger.error("Database operation failed: %s", safe_msg)
        raise RuntimeError(f"Database error: {safe_msg}") from None


def check_db_health(
    uri: Optional[str] = None, db_name: Optional[str] = None
) -> tuple[bool, Optional[str]]:
    """Checks whether MongoDB is reachable and responsive.

    Returns:
        (is_healthy, error_message_or_None)
    """
    try:
        _resolve_uri(uri)
    except ConnectionError as e:
        return False, str(e)

    try:
        db = get_database(uri=uri, db_name=db_name)
        result = db.command("ping")
        if result and result.get("ok") == 1:
            return True, None
        return False, "Unexpected response from database ping."
    except Exception as e:
        return False, sanitize_db_error(e)


# ─── Serialization ───────────────────────────────────────────────────────────


def serialize(value: Any) -> Any:
    """Recursively converts BSON values into JSON-serializable Python types.

    ObjectIds become their hex string, datetimes become ISO-8601, Decimals
    become floats. Anything an LLM tool returns has to survive json.dumps.
    """
    if isinstance(value, ObjectId):
        return str(value)
    if isinstance(value, datetime.datetime):
        return value.isoformat()
    if isinstance(value, datetime.date):
        return value.isoformat()
    if isinstance(value, datetime.time):
        return value.strftime("%H:%M")
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, dict):
        return {k: serialize(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [serialize(v) for v in value]
    return value


def serialize_doc(doc: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Serializes a single Mongo document, exposing `_id` as `id` as well."""
    if doc is None:
        return None
    out = serialize(dict(doc))
    if "_id" in out:
        out["id"] = out["_id"]
    return out


def to_object_id(value: Any) -> Optional[ObjectId]:
    """Parses a value into an ObjectId, returning None when it is not one."""
    if isinstance(value, ObjectId):
        return value
    if not value:
        return None
    try:
        return ObjectId(str(value).strip())
    except (InvalidId, TypeError, ValueError):
        return None


# ─── Collection names ────────────────────────────────────────────────────────
# Mongoose pluralizes model names, so these are what the Node backend created.


class Collections:
    PATIENTS = "patients"
    ADMISSIONS = "admissions"
    MEDICATIONS = "medications"
    PRESCRIPTIONS = "prescriptions"
    DISCHARGE_PACKAGES = "dischargepackages"
    APPOINTMENTS = "appointments"
    USERS = "users"
    SPECIALTIES = "specialties"
    VITALS = "vitals"
    BEDS = "beds"
    AUDIT_LOGS = "auditlogs"
