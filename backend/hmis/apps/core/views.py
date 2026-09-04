from django.db import models
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import filters, viewsets, permissions
from rest_framework.decorators import action
from rest_framework.exceptions import MethodNotAllowed, PermissionDenied
from rest_framework.response import Response
from apps.accounts.permissions import IsAdmin
from .models import AuditLog, Notification
from .serializers import AuditLogSerializer, NotificationSerializer


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
