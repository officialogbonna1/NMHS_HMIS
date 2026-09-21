"""
What the API answers when something goes wrong.

One handler, registered as DRF's `EXCEPTION_HANDLER`, so every endpoint in the
hospital refuses in the same shape and no endpoint has to remember to. It
divides failures into two kinds and treats them completely differently.

**Expected failures are answers.** A wrong field, a record that is not there, a
role that may not do this, a token that has expired, a lock that is still
standing, a delivery that would take more units than the shelf holds — these
are the application working. They keep DRF's own status code and body, gain a
short machine-readable `code` where they did not already carry one, and are not
logged as faults.

**Unexpected failures are bugs**, and are the only thing here that is logged
with a traceback. The caller gets a 500 with a `reference` and nothing else —
no exception class, no message, no stack, no SQL. The reference is in the log
line beside the traceback, so a support call ("it said RF-3f2a9c") lands on the
exact entry without the screen having leaked anything to get there.

What this is deliberately **not** is a blanket `except Exception` that turns
every bug into a tidy message. A `KeyError` in a service is not an operational
condition, and dressing one up as a 400 is how it survives to production. It
reaches this module as a 500 and is logged as loudly as it deserves.

Three failures that would otherwise be 500s are translated here, because they
are conditions a hospital's API genuinely meets rather than faults:

- `Http404` / `ObjectDoesNotExist` — the row is gone or was never theirs to
  see. DRF handles the first; the second is what a service raises when it does
  a `.get()` of its own.
- `IntegrityError` — a uniqueness or foreign-key rule the database holds. 409,
  because the request conflicts with what is already stored; the database's own
  message is **not** passed on, since it names columns and constraints.
- Django's `ValidationError` and `PermissionDenied` — raised by model `save()`
  methods and services all over this codebase (`LockedRecordMixin`,
  `StockRecord.save`, `inventory/services.py`). DRF only understands its own,
  so without this they would be 500s for what is plainly a 400 and a 403.
"""
import logging
import uuid

from django.core.exceptions import (
    ObjectDoesNotExist,
    PermissionDenied as DjangoPermissionDenied,
    ValidationError as DjangoValidationError,
)
from django.db import DatabaseError, IntegrityError
from django.http import Http404
from rest_framework import status
from rest_framework.exceptions import APIException, NotFound, PermissionDenied, ValidationError
from rest_framework.response import Response
from rest_framework.views import exception_handler as drf_exception_handler

logger = logging.getLogger("hmis.api")

#: What the caller is told when the cause is a bug. Deliberately says nothing
#: about it: the detail is in the log, under `reference`.
SERVER_ERROR_DETAIL = (
    "Something went wrong at our end and the action was not completed. "
    "Nothing has been changed. Please try again, and quote the reference "
    "below if it keeps happening."
)


def _reference():
    """Short enough to read down a phone line, unique enough to find."""
    return f"RF-{uuid.uuid4().hex[:8]}"


def _translate(exc):
    """
    Django's own exceptions, as their DRF equivalents.

    Returns a DRF exception, or None to leave `exc` alone. Kept apart from the
    handler so the mapping reads as a list of decisions rather than a stack of
    branches.
    """
    if isinstance(exc, DjangoValidationError):
        # `message_dict` where the model validated per field, `messages`
        # otherwise. Either is the author's own wording, meant to be read.
        detail = getattr(exc, "message_dict", None) or getattr(exc, "messages", None) or str(exc)
        return ValidationError(detail)
    if isinstance(exc, DjangoPermissionDenied):
        return PermissionDenied(str(exc) or "You do not have permission to do this.")
    if isinstance(exc, ObjectDoesNotExist):
        # Never echo the message: `Patient matching query does not exist`
        # confirms what the caller was probing for.
        return NotFound("Not found.")
    return None


def _conflict(exc):
    """
    A database constraint said no.

    409 rather than 500 — the request is well formed and the caller may be able
    to resolve it — with a fixed sentence. The database's own text names the
    table, the column and the constraint, which is internal structure and is
    exactly the kind of thing that must not reach a screen.
    """
    logger.warning("Integrity error answered as 409: %s", exc, exc_info=False)
    conflict = APIException("This conflicts with a record that already exists.")
    conflict.status_code = status.HTTP_409_CONFLICT
    # `APIException.default_code` is "error"; without this the body would say
    # so, and a screen branching on the code could not tell a conflict from
    # anything else.
    conflict.default_code = "conflict"
    return conflict


def _code_for(exc, response):
    """
    A short, stable token the frontend can branch on.

    Services in this codebase already answer with their own (`no_vitals`,
    `insufficient_stock`, `amendment_reason_required`, `login_locked`…), and
    those are left exactly as they are. This only fills in a default for the
    refusals that never had one, so every error body has the same keys.
    """
    body = response.data
    if isinstance(body, dict) and "code" in body:
        # A service chose this — `refund_workflow_required`, `no_vitals`,
        # `insufficient_stock`, `login_locked`. Returned **exactly** as it
        # stands, list or string: DRF wraps a ValidationError's values in
        # lists, and callers (and rule 33's tests) already read that shape.
        # Normalising it here would silently break every one of them.
        return body["code"]
    default = getattr(exc, "default_code", None)
    if default:
        return str(default)
    return {
        400: "invalid", 401: "not_authenticated", 403: "permission_denied",
        404: "not_found", 405: "method_not_allowed", 409: "conflict",
        429: "throttled",
    }.get(response.status_code, "error")


def api_exception_handler(exc, context):
    """
    DRF's handler, plus the translations above and a floor under everything
    else.

    The response body always carries `detail` and `code`; a field-level
    validation error keeps its per-field dictionary alongside them, because
    that is what a form needs to mark up its own inputs.
    """
    translated = _translate(exc)
    if translated is not None:
        exc = translated
    elif isinstance(exc, IntegrityError):
        exc = _conflict(exc)

    response = drf_exception_handler(exc, context)

    if response is not None:
        body = response.data
        if isinstance(body, dict):
            data = dict(body)
        elif isinstance(body, list):
            # DRF renders a bare-message ValidationError as a list.
            data = {"errors": body}
        else:
            data = {}
        data.setdefault("detail", _detail_from(body))
        data["code"] = _code_for(exc, response)
        response.data = data
        return response

    # Nothing above recognised it, so it is not an operational condition: it
    # is a bug, or a database that has gone away. Log it whole — this is the
    # only place in the application that does — and answer with nothing.
    reference = _reference()
    view = context.get("view").__class__.__name__ if context.get("view") else "?"
    request = context.get("request")
    logger.exception(
        "Unhandled exception %s in %s (%s %s) — reference %s",
        type(exc).__name__, view,
        getattr(request, "method", "?"),
        getattr(request, "path", "?"),
        reference,
    )
    detail = SERVER_ERROR_DETAIL
    if isinstance(exc, DatabaseError):
        detail = ("The hospital's database could not be reached and the action was "
                  "not completed. Nothing has been changed. Please try again.")
    return Response(
        {"detail": detail, "code": "server_error", "reference": reference},
        status=status.HTTP_500_INTERNAL_SERVER_ERROR,
    )


def _detail_from(body):
    """A sentence for a body that only had per-field errors in it."""
    if isinstance(body, dict):
        if isinstance(body.get("detail"), str):
            return body["detail"]
        for value in body.values():
            if isinstance(value, list) and value and isinstance(value[0], str):
                return value[0]
            if isinstance(value, str):
                return value
    if isinstance(body, list) and body and isinstance(body[0], str):
        return body[0]
    return "That request could not be completed."


__all__ = ["api_exception_handler", "SERVER_ERROR_DETAIL"]
