"""Binds tool calls to the caller's verified identity.

The appointment agent has a tool, `get_patient_appointments`, whose first
argument is the patient whose appointments to return. Left to the model, that
argument is whatever the conversation produced — and the model's natural move
when it does not know the patient is to *ask*, which means a user can simply
type somebody else's patient ID and be shown their appointments.

An instruction cannot fix that. "Always use the authenticated identity" is a
request, and a request is not an access control. So the identity is bound
here instead: before any tool runs, every argument that names the caller is
overwritten with the identity the HTTP layer verified. Whatever the model
decided is discarded.

This runs *in addition to* the per-record checks inside the tools, which stay
exactly as they are. This closes the hole where the identity itself was the
thing being chosen.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# Arguments that name "who is asking". Any of these is replaced, never merged.
IDENTITY_ARGUMENTS = frozenset(
    {
        "authenticated_patient_id",
        "patient_id",
    }
)

# Session-state keys the services write the verified identity under.
IDENTITY_STATE_KEYS = ("patient_id", "user_id")

# A verified patient identity, as the Node backend issues it.
PATIENT_ID_RE = re.compile(r"^PAT[-_]?[0-9]", re.IGNORECASE)


def _verified_identity(ctx: Any) -> Optional[str]:
    """Reads the verified caller identity out of the ADK session state."""
    state = getattr(ctx, "state", None)
    if state is None:
        return None
    for key in IDENTITY_STATE_KEYS:
        try:
            value = state.get(key)
        except Exception:
            value = None
        if value and str(value).strip():
            return str(value).strip()
    return None


def is_patient_identity(identity: Optional[str]) -> bool:
    """True when the identity is a patient chart rather than staff or a guest.

    Staff call through as `staff:<role>:<id>` and guests as `guest_visitor`.
    Neither names a chart, so neither may be substituted into a patient
    argument — a staff member acting on a named patient must keep that
    patient's id, which the backend has already resolved for them.
    """
    if not identity:
        return False
    return bool(PATIENT_ID_RE.match(identity))


def bind_verified_identity(tool: Any, args: Dict[str, Any], ctx: Any) -> None:
    """ADK `before_tool_callback`: forces identity arguments to the verified caller.

    Mutates `args` in place and returns None so the tool runs normally.
    """
    identity = _verified_identity(ctx)
    if not is_patient_identity(identity):
        # Nothing trustworthy to substitute. The tools' own per-record
        # authorisation still applies.
        return None

    for name in IDENTITY_ARGUMENTS:
        if name not in args:
            continue
        supplied = args.get(name)
        if supplied is not None and str(supplied).strip() == identity:
            continue
        if supplied:
            logger.warning(
                "Tool %s was called with %s=%r; overriding with the verified caller.",
                getattr(tool, "name", tool),
                name,
                str(supplied)[:64],
            )
        args[name] = identity

    return None
