"""
`Notification.raised_for_role` — the role the recipient held when it was raised.

A person's feed is their notifications *in the role they hold now*
(`NotificationQuerySet.for_user`). Rows that already exist are stamped with the
recipient's current role only where nothing records a change of role since the
notification was raised:

* Django admin's change log naming the Role field, or
* an HMIS account edit (`user.updated`), whose audit row does not say which
  field changed — so it is counted, erring towards privacy.

Where one of those comes later than the notification, the row is left blank. It
is kept, an admin's whole-system view still lists it, and it no longer appears
in a feed that now belongs to a different role. Nothing is inferred from a
title, a category or a link. Only the new column is written.
"""
import json

from django.db import migrations, models

ADMIN_CHANGE = 2


def _names_role(message):
    try:
        entries = json.loads(message or "[]")
    except ValueError:
        return "role" in (message or "").lower()
    if not isinstance(entries, list):
        return False
    for entry in entries:
        fields = ((entry or {}).get("changed") or {}).get("fields") or []
        if any(str(field).lower() == "role" for field in fields):
            return True
    return False


def stamp_existing(apps, schema_editor):
    Notification = apps.get_model("core", "Notification")
    AuditLog = apps.get_model("core", "AuditLog")
    User = apps.get_model("accounts", "User")
    LogEntry = apps.get_model("admin", "LogEntry")
    ContentType = apps.get_model("contenttypes", "ContentType")

    last_change = {}

    def seen(object_id, when):
        if not str(object_id).isdigit() or when is None:
            return
        user_id = int(object_id)
        if user_id not in last_change or when > last_change[user_id]:
            last_change[user_id] = when

    user_type = ContentType.objects.filter(app_label="accounts", model="user").first()
    if user_type is not None:
        entries = LogEntry.objects.filter(content_type_id=user_type.pk, action_flag=ADMIN_CHANGE)
        for object_id, when, message in entries.values_list("object_id", "action_time", "change_message"):
            if _names_role(message):
                seen(object_id, when)
    for object_id, when in AuditLog.objects.filter(action="user.updated").values_list("object_id", "created_at"):
        seen(object_id, when)

    for user_id, role in User.objects.values_list("pk", "role"):
        rows = Notification.objects.filter(recipient_id=user_id)
        if user_id in last_change:
            rows = rows.filter(created_at__gt=last_change[user_id])
        rows.update(raised_for_role=role or "")


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0005_pos_discount_policy"),
        ("accounts", "0005_user_pos_discount_authorized"),
        ("admin", "0003_logentry_add_action_flag_choices"),
        ("contenttypes", "0002_remove_content_type_name"),
    ]

    operations = [
        migrations.AddField(
            model_name="notification",
            name="raised_for_role",
            field=models.CharField(
                blank=True, default="", max_length=20,
                help_text="The recipient's role when this was raised; their feed shows it only while they hold that role."),
        ),
        migrations.RunPython(stamp_existing, migrations.RunPython.noop),
    ]
