"""
Gives a member of staff the number described in `apps/core/identifiers.py`.

Added nullable and filled a row at a time before the unique index goes on, for
the same reason the patient UUID was: one `AddField` with a computed default
writes one value to every row.

Only accounts that are actually staff are numbered — an HMIS role, or the
superuser flag. A login with neither reaches nothing in the application and is
left without a number rather than being counted as an employee. Existing
accounts keep any number they already hold; none is issued twice, because each
is taken from the account's own primary key.
"""

from django.db import migrations, models

from apps.core import identifiers


def issue_staff_numbers(apps, schema_editor):
    User = apps.get_model("accounts", "User")
    for user in User.objects.order_by("pk").only("pk", "role", "is_superuser", "staff_number"):
        if not (user.role or user.is_superuser):
            continue
        adopted = identifiers.adopt_number(user.staff_number, identifiers.STAFF, user.pk)
        if adopted != user.staff_number:
            User.objects.filter(pk=user.pk).update(staff_number=adopted)


def withdraw_staff_numbers(apps, schema_editor):
    User = apps.get_model("accounts", "User")
    User.objects.update(staff_number=None)


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0003_alter_user_role"),
    ]

    operations = [
        migrations.AddField(
            model_name="user",
            name="staff_number",
            field=models.CharField(blank=True, editable=False, max_length=20, null=True),
        ),
        migrations.RunPython(issue_staff_numbers, withdraw_staff_numbers),
        migrations.AlterField(
            model_name="user",
            name="staff_number",
            field=models.CharField(blank=True, editable=False, max_length=20, null=True,
                                   unique=True),
        ),
    ]
