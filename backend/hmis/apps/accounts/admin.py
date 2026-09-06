from django.contrib import admin, messages
from django.contrib.auth.admin import UserAdmin
from django.urls import reverse
from django.utils.html import format_html

from .models import User


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

    fieldsets = UserAdmin.fieldsets + (
        ("Staff number", {
            "fields": ("staff_number",),
            "description": "Issued once, on the first save that gives this account a role. "
                           "It is what a staff profile and a report identify the person by, "
                           "and it never moves.",
        }),
        ("HMIS role", {
            "fields": ("role", "department", "sensitive_record_access", "must_change_password"),
            "description": "Role decides what this account can reach in the app.",
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
    list_filter = ["role", "is_active", "is_staff", "must_change_password"]
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
