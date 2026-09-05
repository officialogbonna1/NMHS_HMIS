from django.db import models
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import filters, viewsets, permissions
from rest_framework.decorators import action
from rest_framework.exceptions import MethodNotAllowed, PermissionDenied, ValidationError
from rest_framework.response import Response
from apps.accounts.permissions import IsAdmin
from .services import audit_event
from .models import AuditLog, HospitalSettings, Notification, NotificationSetting
from .serializers import (
    AuditLogSerializer, HospitalSettingsSerializer, NotificationSerializer,
    NotificationSettingSerializer,
)


class NotificationViewSet(viewsets.ModelViewSet):
    """
    Your notifications — and, for an admin, everyone's.

    The default is always your own inbox: that is what the bell counts, what
    "mark all as read" clears, and the only thing you can mark read. An admin
    asking for `?scope=all` gets the whole hospital's, read-only, for
    oversight — "did the lab ever get told?" is a question somebody has to be
    able to answer.

    Those two are kept deliberately separate. If the admin's own bell counted
    every notification in the system it would show hundreds, never clear, and
    stop meaning anything; and one press of "mark all as read" would mark
    every nurse's and doctor's notifications read behind their backs.
    """
    serializer_class = NotificationSerializer
    permission_classes = [permissions.IsAuthenticated]
    http_method_names = ["get", "post", "patch", "head", "options"]
    filter_backends = [DjangoFilterBackend, filters.SearchFilter]
    filterset_fields = ["category", "is_read", "recipient"]
    search_fields = ["title", "message", "recipient__username",
                     "recipient__first_name", "recipient__last_name"]

    def _wants_everyone(self):
        return self.request.query_params.get("scope") == "all"

    def _mine(self):
        return Notification.objects.filter(recipient=self.request.user)

    def get_queryset(self):
        if self._wants_everyone():
            if not self.request.user.is_admin:
                raise PermissionDenied("Only an admin can read other people's notifications.")
            return Notification.objects.select_related("recipient")
        return self._mine().select_related("recipient")

    def create(self, request, *args, **kwargs):
        # Notifications are only ever created server-side via core.services.notify().
        raise MethodNotAllowed("POST")

    def partial_update(self, request, *args, **kwargs):
        """
        Marking one as read. Only ever your own: an admin reading the whole
        system is looking, not answering somebody else's mail.
        """
        if not self._mine().filter(pk=kwargs.get("pk")).exists():
            raise PermissionDenied("You can only mark your own notifications as read.")
        return super().partial_update(request, *args, **kwargs)

    @action(detail=False, methods=["post"])
    def mark_all_read(self, request):
        # `_mine()`, never `get_queryset()` — an admin must not be able to
        # clear the whole hospital's unread notifications with one press.
        self._mine().update(is_read=True)
        return Response(status=204)

    @action(detail=False, methods=["get"], url_path="unread-count")
    def unread_count(self, request):
        """
        One number, polled by the shell so the bell in the header can say
        there is something waiting. Deliberately not the notification list:
        this is fetched on a timer from every page — and deliberately your
        own, admin or not, because a bell that counts other people's work is
        one you learn to ignore.
        """
        return Response({"unread": self._mine().filter(is_read=False).count()})

    @action(detail=False, methods=["get"], permission_classes=[IsAdmin])
    def overview(self, request):
        """
        What the whole system has been telling people, summarised: how many
        notifications each role and each person has, and how many are still
        unread. Answers "is anybody actually reading the lab's queue?" without
        scrolling a list of thousands.
        """
        rows = (Notification.objects.values("recipient__username", "recipient__role")
                .annotate(total=models.Count("id"),
                          unread=models.Count("id", filter=models.Q(is_read=False)))
                .order_by("recipient__role", "recipient__username"))
        by_category = (Notification.objects.values("category")
                       .annotate(total=models.Count("id"),
                                 unread=models.Count("id", filter=models.Q(is_read=False)))
                       .order_by("-total"))
        return Response({
            "people": [
                {"username": r["recipient__username"], "role": r["recipient__role"],
                 "total": r["total"], "unread": r["unread"]}
                for r in rows
            ],
            "categories": list(by_category),
            "total": Notification.objects.count(),
            "unread": Notification.objects.filter(is_read=False).count(),
        })


class AuditLogViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = AuditLogSerializer
    permission_classes = [IsAdmin]
    queryset = AuditLog.objects.select_related("actor", "content_type")


class HospitalSettingsViewSet(viewsets.ModelViewSet):
    """
    The hospital's own settings — one row, everybody reads it, an admin edits it.

    Read by everyone on purpose: the letterhead on a printed document and the
    hospital's name in the application header come from here, so every signed-in
    user needs it. Only an admin may change it.

    `GET /hospital-settings/current/` is the shape the frontend asks for — the
    row itself rather than a list of one.
    """
    serializer_class = HospitalSettingsSerializer
    queryset = HospitalSettings.objects.all()
    http_method_names = ["get", "put", "patch", "head", "options"]

    def get_permissions(self):
        if self.request.method in permissions.SAFE_METHODS:
            return [permissions.IsAuthenticated()]
        return [IsAdmin()]

    @action(detail=False, methods=["get", "patch"])
    def current(self, request):
        """The single row, created with the defaults if it is not there yet."""
        settings_row = HospitalSettings.load()
        if request.method == "PATCH":
            if not IsAdmin().has_permission(request, self):
                raise PermissionDenied("Only an admin can change the hospital settings.")
            serializer = self.get_serializer(settings_row, data=request.data, partial=True)
            serializer.is_valid(raise_exception=True)
            serializer.save()
            audit_event(actor=request.user, action="config.hospital_settings_updated",
                        instance=settings_row, details=request.data, request=request)
            return Response(serializer.data)
        return Response(self.get_serializer(settings_row).data)


class NotificationSettingViewSet(viewsets.ModelViewSet):
    """
    Which categories of notification the hospital sends.

    Read by any signed-in user — a nurse may reasonably want to know why the
    bell is quiet — and changed by an admin. `clinical` cannot be switched
    off here or anywhere: `core.services.notify()` ignores a setting that
    tries to, because a result reaching the doctor who ordered it is not a
    preference. The API says so rather than silently keeping it on.
    """
    serializer_class = NotificationSettingSerializer
    queryset = NotificationSetting.objects.all()
    http_method_names = ["get", "patch", "head", "options"]

    def get_permissions(self):
        if self.request.method in permissions.SAFE_METHODS:
            return [permissions.IsAuthenticated()]
        return [IsAdmin()]

    def perform_update(self, serializer):
        from .services import ALWAYS_ON

        instance = serializer.instance
        if instance.category in ALWAYS_ON and serializer.validated_data.get("is_enabled") is False:
            raise ValidationError({
                "is_enabled": f"{instance.get_category_display()} notifications cannot be "
                              f"switched off — a result has to reach the clinician who asked."})
        setting = serializer.save()
        audit_event(actor=self.request.user, action="config.notifications_updated",
                    instance=setting,
                    details={"category": setting.category, "enabled": setting.is_enabled},
                    request=self.request)
