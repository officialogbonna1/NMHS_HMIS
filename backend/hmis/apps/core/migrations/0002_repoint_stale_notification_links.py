import re

from django.db import migrations

# Routing notifications used to link to /visits/<id>, a page the frontend
# never had. Point them at the patient the visit belongs to, which is what
# the recipient wanted to open anyway.
VISIT_URL = re.compile(r"^/visits/(\d+)/?$")


def repoint(apps, schema_editor):
    Notification = apps.get_model("core", "Notification")
    Visit = apps.get_model("workflow", "Visit")

    for note in Notification.objects.exclude(action_url="").iterator():
        match = VISIT_URL.match(note.action_url)
        if not match:
            continue
        visit = Visit.objects.filter(pk=int(match.group(1))).first()
        # A nurse works from their own station; everyone else opens the chart.
        # Recipients whose visit has since been deleted lose the link rather
        # than keeping one that goes nowhere.
        if not visit:
            note.action_url = ""
        elif note.recipient.role == "nurse":
            note.action_url = "/vitals"
        else:
            note.action_url = f"/patients/{visit.patient_id}"
        note.save(update_fields=["action_url"])


def noop(apps, schema_editor):
    """Not reversible: the original visit id is gone once rewritten."""


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0001_initial"),
        ("workflow", "0002_patientroute_purpose"),
    ]
    operations = [migrations.RunPython(repoint, noop)]
