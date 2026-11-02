"""Collection inspection for MongoDB.

MongoDB has no information_schema, so the shape of a collection is inferred by
sampling documents and recording which fields appear and with what BSON types.
The sample is deliberately small and read-only: this tool exists so an agent
can orient itself, not so it can trawl patient data.
"""

from __future__ import annotations

import datetime
import logging
import re
from typing import Any, Dict, List, Optional

from bson import ObjectId

from database import Collections, get_db, sanitize_db_error

logger = logging.getLogger(__name__)

SAFE_IDENTIFIER_REGEX = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_.]*$")

DEFAULT_INSPECT_COLLECTIONS = [
    Collections.USERS,
    Collections.SPECIALTIES,
    Collections.APPOINTMENTS,
    Collections.PATIENTS,
]

# Only these may be inspected. Without an allowlist, a prompt-injected
# "inspect the auditlogs" would be honoured.
ALLOWED_COLLECTIONS = {
    Collections.PATIENTS,
    Collections.ADMISSIONS,
    Collections.MEDICATIONS,
    Collections.DISCHARGE_PACKAGES,
    Collections.APPOINTMENTS,
    Collections.USERS,
    Collections.SPECIALTIES,
    Collections.VITALS,
    Collections.BEDS,
}

SAMPLE_SIZE = 25
# Never echo these back, whatever a sampled document happens to contain.
REDACTED_FIELDS = {"passwordHash", "password", "token", "secret"}


def _bson_type(value: Any) -> str:
    """Names the BSON/Python type of a sampled value."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, ObjectId):
        return "objectId"
    if isinstance(value, int):
        return "int"
    if isinstance(value, float):
        return "double"
    if isinstance(value, str):
        return "string"
    if isinstance(value, datetime.datetime):
        return "date"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return type(value).__name__


def _walk(doc: Dict[str, Any], fields: Dict[str, Dict[str, Any]], prefix: str = "") -> None:
    """Records every field path in a document and the types seen at it."""
    for key, value in doc.items():
        path = f"{prefix}{key}"
        entry = fields.setdefault(path, {"types": set(), "present": 0, "redacted": False})
        entry["present"] += 1

        if key in REDACTED_FIELDS:
            entry["types"].add("string")
            entry["redacted"] = True
            continue

        entry["types"].add(_bson_type(value))

        if isinstance(value, dict):
            _walk(value, fields, prefix=f"{path}.")
        elif isinstance(value, list) and value and isinstance(value[0], dict):
            # Describe the array's element shape from its first entry.
            _walk(value[0], fields, prefix=f"{path}[].")


def inspect_database_schema(
    collection_names: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Inspects the actual MongoDB collection shapes for the requested collections.

    Samples a small number of documents per collection and reports the field
    paths, observed types, how often each field is present, and the declared
    indexes. Read-only.

    Args:
        collection_names: Optional list of collection names to inspect. Defaults to
            ['users', 'specialties', 'appointments', 'patients']. Allowed values are
            patients, admissions, medications, dischargepackages, appointments,
            users, specialties, vitals and beds.

    Returns:
        Structured dictionary describing each requested collection.
    """
    requested = (
        collection_names if collection_names is not None else DEFAULT_INSPECT_COLLECTIONS
    )

    validated: List[str] = []
    invalid: List[str] = []
    for name in requested:
        text = str(name).strip() if name is not None else ""
        if text and SAFE_IDENTIFIER_REGEX.match(text) and text in ALLOWED_COLLECTIONS:
            validated.append(text)
        else:
            invalid.append(str(name))

    if invalid:
        return {
            "status": "error",
            "message": (
                f"Invalid or disallowed collection names: {invalid}. "
                f"Allowed collections are: {sorted(ALLOWED_COLLECTIONS)}."
            ),
            "collections": {},
        }

    result: Dict[str, Any] = {}

    try:
        with get_db() as db:
            existing = set(db.list_collection_names())

            for name in validated:
                if name not in existing:
                    result[name] = {
                        "exists": False,
                        "document_count": 0,
                        "fields": [],
                        "indexes": [],
                    }
                    continue

                collection = db[name]
                projection = {f: 0 for f in REDACTED_FIELDS}
                sample = list(collection.find({}, projection).limit(SAMPLE_SIZE))

                fields: Dict[str, Dict[str, Any]] = {}
                for doc in sample:
                    _walk(doc, fields)

                sampled = len(sample) or 1
                field_list = [
                    {
                        "field": path,
                        "types": sorted(meta["types"]),
                        "present_in_sample": meta["present"],
                        "always_present": meta["present"] == sampled,
                        "redacted": meta["redacted"],
                    }
                    for path, meta in sorted(fields.items())
                ]

                indexes = []
                try:
                    for index in collection.list_indexes():
                        indexes.append(
                            {
                                "name": index.get("name"),
                                "keys": list(index.get("key", {}).keys()),
                                "unique": bool(index.get("unique", False)),
                            }
                        )
                except Exception:
                    indexes = []

                result[name] = {
                    "exists": True,
                    "document_count": collection.estimated_document_count(),
                    "sampled_documents": len(sample),
                    "fields": field_list,
                    "indexes": indexes,
                }

        return {"status": "success", "collections": result}

    except Exception as e:
        safe = sanitize_db_error(e)
        logger.error("Failed to inspect collections: %s", safe)
        return {
            "status": "error",
            "message": f"Failed to inspect database schema: {safe}",
            "collections": {},
        }
