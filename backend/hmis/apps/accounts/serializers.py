from rest_framework import serializers

from apps.departments.models import Department

from .models import User


class AuthorizedDepartmentSerializer(serializers.Serializer):
    """A department this account may work in — enough to label a context."""
    id = serializers.IntegerField(read_only=True)
    code = serializers.CharField(read_only=True)
    name = serializers.CharField(read_only=True)


class UserSerializer(serializers.ModelSerializer):
    """
    The signed-in account, as `/auth/me/` returns it.

    **`department` and `authorized_departments` are two different things**, and
    both are here on purpose. `department` is the free-text
    primary/organisational label the Users page has always shown and edited —
    unchanged, so every existing consumer keeps working. `authorized_departments`
    is *where this person may work*, read from the `Department.staff` relation an
    administrator sets (`accounts/departments.py`).

    It is **read-only, and it authorises nothing by itself.** The server never
    trusts it coming back: every department-sensitive endpoint asks
    `works_in(request.user, …)` against the database. A client that edits this
    list in memory changes what its own screen offers and nothing else.
    """
    authorized_departments = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = ["id", "staff_number", "username", "first_name", "last_name", "email",
                  "role", "department", "authorized_departments", "must_change_password",
                  "sensitive_record_access", "pos_discount_authorized", "last_login"]
        # Read by the till to show the Discount action; granted in Django admin
        # only, never by the person it describes. `authorized_departments` is
        # the same kind of field for the same reason — a person who could post
        # themselves to a department would be granting their own access.
        read_only_fields = ["pos_discount_authorized", "authorized_departments"]

    def get_authorized_departments(self, obj):
        from .departments import authorized_departments

        return AuthorizedDepartmentSerializer(authorized_departments(obj), many=True).data


class UserDirectorySerializer(serializers.ModelSerializer):
    """Minimal, safe-for-any-authenticated-staff lookup (e.g. picking a
    doctor when booking an appointment) — no email/department/activity."""
    class Meta:
        model = User
        fields = ["id", "staff_number", "first_name", "last_name", "role"]


class UserAdminSerializer(serializers.ModelSerializer):
    """
    Used by UserViewSet. Admin-only: creates/edits accounts, never exposes
    or accepts a password here — that goes through the set_password action.

    **It writes `authorized_departments`**, so an administrator posts somebody
    to a second department from the Users page rather than having to open
    Django admin. It is the *same relation* Django admin edits
    (`Department.staff`) through the same helper — two front doors onto one
    model, which is rule 31, not a second way of granting access.

    Who may: `UserViewSet.get_permissions` is `IsAdmin()` for every write, and
    this field is absent from `UserSerializer` — the payload the signed-in
    account reads about itself — so nobody can post themselves anywhere.
    """
    password = serializers.CharField(write_only=True, required=False, allow_blank=True)
    # The reverse side of `Department.staff`. Named for what it means rather
    # than for the relation, because "where may this person work" is the
    # question an administrator is answering.
    authorized_departments = serializers.PrimaryKeyRelatedField(
        many=True, required=False, source="department_memberships",
        queryset=Department.objects.filter(is_active=True))
    authorized_department_names = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = ["id", "staff_number", "username", "first_name", "last_name", "email", "role", "department",
                  "authorized_departments", "authorized_department_names",
                  "is_active", "must_change_password", "sensitive_record_access", "last_login", "password"]
        # `staff_number` is issued by the model on the first save that makes the
        # account staff (see `apps/core/identifiers.py`) — an identifier a form
        # can retype is not one, so admin reads it and never writes it.
        read_only_fields = ["last_login", "staff_number", "authorized_department_names"]

    def get_authorized_department_names(self, obj):
        """What the list column shows, so the page needs no second request."""
        from .departments import authorized_departments

        return [d.name for d in authorized_departments(obj)]

    def create(self, validated_data):
        password = validated_data.pop("password", None)
        if not password:
            raise serializers.ValidationError({"password": "Required when creating a user."})
        # The departments are a many-to-many and cannot be passed to the
        # constructor — they are written once the row exists, which is what
        # DRF's own `create` does for every other m2m. Building `User(**…)`
        # with them in answered 500.
        departments = validated_data.pop("department_memberships", None)
        user = User(**validated_data)
        user.set_password(password)
        user.save()
        if departments:
            user.department_memberships.set(departments)
        return user

    def update(self, instance, validated_data):
        validated_data.pop("password", None)
        return super().update(instance, validated_data)
