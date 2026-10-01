import os
from celery import Celery
from celery.signals import before_task_publish, task_postrun, task_prerun

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "hmis.settings")

app = Celery("hmis")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()


# --------------------------------------------------------- request correlation
#
# A task carries the X-Request-ID of the request that queued it, in a message
# header of its own — never in its arguments, which Celery does not log and
# which may hold a rendered email. In the worker that ID (or, for a Beat task
# with no request behind it, the task's own id) is put on every log line the
# task writes, so an administrator can follow one request into the worker.

_HEADER = "hmis_request_id"


@before_task_publish.connect
def _carry_request_id(headers=None, **_):
    from apps.core.logging import request_id_var

    if headers is not None:
        current = request_id_var.get()
        if current and current != "-":
            headers.setdefault(_HEADER, current)


_tokens = {}


@task_prerun.connect
def _adopt_request_id(task_id=None, task=None, **_):
    from apps.core.logging import request_id_var

    carried = None
    if task is not None:
        # Celery exposes a custom message header either as a request attribute
        # or under `request.headers`, depending on the protocol in use.
        carried = (getattr(task.request, _HEADER, None)
                   or (getattr(task.request, "headers", None) or {}).get(_HEADER))
    _tokens[task_id] = request_id_var.set(carried or f"task-{task_id}")


@task_postrun.connect
def _drop_request_id(task_id=None, **_):
    from apps.core.logging import request_id_var

    token = _tokens.pop(task_id, None)
    if token is not None:
        request_id_var.reset(token)
