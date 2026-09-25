from django import forms
from django.contrib import admin, messages
from django.contrib.auth.admin import UserAdmin
from django.contrib.auth.forms import UserChangeForm
from django.urls import reverse
from django.utils.html import format_html

from apps.departments.models import Department

from .models import User


class StaffForm(UserChangeForm):
    """
    The staff form, plus **Authorized departments**.

    It subclasses Django's own `UserChangeForm` rather than a bare `ModelForm`:
    that is what keeps the password column a read-only hash link instead of a
    text box, which this admin's docstring exists to warn about.

    `Department.staff` is declared on `Department`, so this is the reverse side
    of an existing many-to-many and Django cannot put it on the user form by
    itself. This is the standard pattern for that: an unbound
    `ModelMultipleChoiceField` that reads the relation on load and writes it on
    save. No new model, no new column, no second relation.
    """
    authorized_departments = forms.ModelMultipleChoiceField(
        queryset=Department.objects.filter(is_active=True),
        required=False,
        widget=admin.widgets.FilteredSelectMultiple("departments", is_stacked=False),
        help_text="Where this member of staff may work. Their role still decides "
                  "what they may do there — a doctor authorised for Maternity is a "
                  "doctor in Maternity, not a midwife. Leave empty for staff who "
                  "work no department of their own.",
    )

    class Meta(UserChangeForm.Meta):
        model = User

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk:
            self.fields["authorized_departments"].initial = (
                self.instance.department_memberships.all())

    def save(self, commit=True):
        user = super().save(commit=commit)

        def write_departments():
            chosen = self.cleaned_data.get("authorized_departments")
            if chosen is None:
                return
            # Only the departments this form manages: an inactive one somebody
            # was posted to is left alone rather than silently revoked, because
            # the widget never offered it to be unticked.
            offered = set(self.fields["authorized_departments"].queryset)
            keep = set(user.department_memberships.all()) - offered
            user.department_memberships.set(set(chosen) | keep)

        if commit:
            write_departments()
        else:
            # A deferred save (the add form) runs this once the row exists.
            self.save_m2m_departments = write_departments
        return user


@admin.register(User)
class HMISUserAdmin(UserAdmin):
    """
    Staff accounts. The HMIS role — not `is_staff`, not superuser — is what
    the API authorises against, so it is on the add form as well as the edit
    one; a user created without a role can log in and reach nothing.

    Passwords are set through Django's own change-password form (the
    "Set password" link on each row, or the link beside the password field),
    which hashes properly. Never expose the `password` column for editing:
    typing a plain string into it stores an unusable hash and locks the
    person out.
    """

    form = StaffForm

    def save_related(self, request, form, formsets, change):
        """
        Write the authorised departments once the row exists.

        On the add form Django saves the user with `commit=False` first, so the
        many-to-many cannot be written yet; `StaffForm` defers it and this is
        where it runs — the same place Django writes every other m2m.
        """
        super().save_related(request, form, formsets, change)
        deferred = getattr(form, "save_m2m_departments", None)
        if deferred is not None:
            deferred()

    fieldsets = UserAdmin.fieldsets + (
        ("Staff number", {
            "fields": ("staff_number",),
            "description": "Issued once, on the first save that gives this account a role. "
                           "It is what a staff profile and a report identify the person by, "
                           "and it never moves.",
        }),
        ("HMIS role", {
            "fields": ("role", "sensitive_record_access", "must_change_password"),
            "description": "Role decides what this account can reach in the app.",
        }),
        ("Department & access", {
            "fields": ("department", "authorized_departments"),
            "description": "<b>Primary department</b> is the organisational label shown on "
                           "the staff list — free text, and not an authorisation. "
                           "<b>Authorized departments</b> decide which departments this "
                           "member of staff may work in; role permissions still apply. "
                           "Staff cannot change this for themselves: it is set here and "
                           "is on no form in the application.",
        }),
        ("Pharmacy POS", {
            "fields": ("pos_discount_authorized",),
            "description": "Cashiers, accountants and administrators can already discount at the "
                           "pharmacy till. Tick this to let one more person — a pharmacist, say — "
                           "apply discounts too, within the limits set under Hospital settings → "
                           "POS discount configuration. It does <strong>not</strong> let them "
                           "approve a discount above those limits; that still needs an "
                           "accountant or an administrator. Untick it to take the permission "
                           "away — the till refuses their next discount.",
        }),
    )
    # Django's default add form asks only for username and password. A staff
    # account is useless without a role, so it is asked for up front.
    add_fieldsets = (
        (None, {
            "classes": ("wide",),
            "fields": ("username", "password1", "password2"),
        }),
        ("Who they are", {
            "classes": ("wide",),
            "fields": ("first_name", "last_name", "email"),
            "description": "Full names show on every note, reading and hand-off — "
                           "without them the app falls back to the username.",
        }),
        ("HMIS role", {
            "classes": ("wide",),
            "fields": ("role", "department", "must_change_password"),
        }),
    )

    list_display = ["staff_number", "username", "full_name", "role", "department", "is_active", "set_password_link"]
    list_filter = ["role", "is_active", "is_staff", "must_change_password", "pos_discount_authorized"]
    search_fields = ["staff_number", "username", "first_name", "last_name", "email"]
    readonly_fields = ["staff_number"]
    ordering = ["last_name", "first_name", "username"]
    list_per_page = 50
    actions = ["require_password_change", "deactivate_accounts", "activate_accounts"]

    @admin.display(description="Name", ordering="last_name")
    def full_name(self, obj):
        return obj.get_full_name() or "—"

    @admin.display(description="Password")
    def set_password_link(self, obj):
        """
        UserAdmin.get_urls hardcodes this route's name as
        `auth_user_password_change` whatever model it is registered for — it
        is not derived from the model, so it must not be built from one.
        """
        if not obj.pk:
            return "—"
        return format_html(
            '<a class="button" href="{}">Set password</a>',
            reverse("admin:auth_user_password_change", args=[obj.pk]),
        )

    @admin.action(description="Require a password change at next login")
    def require_password_change(self, request, queryset):
        updated = queryset.update(must_change_password=True)
        self.message_user(request, f"{updated} account(s) must change their password next login.")

    @admin.action(description="Deactivate selected accounts")
    def deactivate_accounts(self, request, queryset):
        # Locking yourself out of the admin mid-session is not a recoverable
        # mistake from inside the admin.
        queryset = queryset.exclude(pk=request.user.pk)
        updated = queryset.update(is_active=False)
        self.message_user(request, f"{updated} account(s) deactivated. Your own was left alone.",
                          messages.WARNING)

    @admin.action(description="Reactivate selected accounts")
    def activate_accounts(self, request, queryset):
        updated = queryset.update(is_active=True)
        self.message_user(request, f"{updated} account(s) reactivated.")
