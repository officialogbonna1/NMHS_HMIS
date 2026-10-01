"""
`GET /healthz/` — is this process able to serve the hospital?

For a load balancer, an uptime monitor or `curl` on the server. It answers one
word and a status code: `{"status": "healthy"}` with 200 when the database
answers a trivial query, `{"status": "unhealthy"}` with 503 when it does not.
It says nothing else — no versions, hosts, credentials, exception text or
timings — because it is unauthenticated: the reason for a failure goes to the
server log, under the request ID, for an administrator to read.

Redis is deliberately not part of the answer. Nothing a user does stops when it
is down (the login lockout fails open, email is sent inline), so reporting the
whole application unhealthy over it would page somebody for a degradation.
It lives outside `/api/` so the API's every-endpoint permission walk
(test_api_permissions) is unchanged, and is exempt from the HTTPS redirect so a
probe on the loopback interface behind the proxy reaches it.
"""
import logging

from django.db import connection
from django.http import JsonResponse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET

logger = logging.getLogger("hmis.health")


@never_cache
@require_GET
def healthz(request):
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
    except Exception as exc:
        logger.error("Health check failed: database unavailable (%s)", type(exc).__name__)
        return JsonResponse({"status": "unhealthy"}, status=503)
    return JsonResponse({"status": "healthy"})
