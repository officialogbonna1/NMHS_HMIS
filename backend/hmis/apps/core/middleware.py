"""
A request ID for every request, and one safe log line about it.

The ID comes from the proxy's `X-Request-ID` when it sends a well-formed one
(so an Nginx log line and a Django log line share it) and is generated here
otherwise. It is put on every log record of the request (`RequestIdFilter`),
handed to any Celery task the request queues (hmis/celery.py), and returned to
the browser in the `X-Request-ID` response header. It is a random token: it
carries no patient, user or credential.

The request line is the application's own access log — method, path, status,
duration, user id and request id — and deliberately nothing else: never the
query string (patient searches by name and phone travel in it), never a header,
a cookie or a body.
"""
import logging
import re
import time
import uuid

from django.conf import settings

from .logging import request_id_var

HEADER = "X-Request-ID"
_VALID = re.compile(r"^[A-Za-z0-9._-]{8,64}$")

request_logger = logging.getLogger("hmis.request")


def _incoming(request):
    value = request.META.get("HTTP_X_REQUEST_ID", "")
    return value if _VALID.match(value) else ""


class RequestIdMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request_id = _incoming(request) or uuid.uuid4().hex
        request.request_id = request_id
        token = request_id_var.set(request_id)
        started = time.monotonic()
        try:
            response = self.get_response(request)
            response[HEADER] = request_id
            if getattr(settings, "REQUEST_LOGGING", False):
                user = getattr(request, "user", None)
                request_logger.info(
                    "%s %s %s %dms user=%s",
                    request.method, request.path, response.status_code,
                    int((time.monotonic() - started) * 1000),
                    getattr(user, "pk", None) if getattr(user, "is_authenticated", False) else "-",
                )
            return response
        finally:
            request_id_var.reset(token)


class AdminNetworkMiddleware:
    """
    Django Admin only from listed networks — the restriction an Nginx
    `allow`/`deny` gave on a server, for hosts (Render) where nothing sits in
    front to do it. Off unless `HMIS_ADMIN_ALLOWED_NETWORKS` lists CIDR ranges.

    The address is the one the login lockout trusts (`lockout.client_ip`, i.e.
    `HMIS_CLIENT_IP_HEADER` on Render), so it cannot be forged by sending an
    X-Forwarded-For. Anyone else gets a plain 404: the back office does not
    advertise that it exists. The HMIS API and its own administration screens
    are unaffected — they are role-checked on every request as before.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        networks = getattr(settings, "HMIS_ADMIN_ALLOWED_NETWORKS", ())
        if networks and request.path.startswith("/admin/"):
            import ipaddress

            from django.http import HttpResponseNotFound

            from apps.accounts.lockout import client_ip

            address = client_ip(request)
            try:
                allowed = any(ipaddress.ip_address(address) in net for net in networks)
            except ValueError:
                allowed = False
            if not allowed:
                logging.getLogger("hmis.security").warning(
                    "Django Admin refused outside the allowed networks: client=%s path=%s",
                    address, request.path)
                return HttpResponseNotFound("Not found.")
        return self.get_response(request)
