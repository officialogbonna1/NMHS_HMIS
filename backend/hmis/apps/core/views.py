from rest_framework import viewsets, permissions
from rest_framework.decorators import action
from rest_framework.exceptions import MethodNotAllowed
from rest_framework.response import Response
from apps.accounts.permissions import IsAdmin
from .models import AuditLog, Notification
from .serializers import AuditLogSerializer, NotificationSerializer


class NotificationViewSet(viewsets.ModelViewSet):
    serializer_class = NotificationSerializer
    permission_classes = [permissions.IsAuthenticated]
    http_method_names = ["get", "post", "patch", "head", "options"]
    def get_queryset(self): return Notification.objects.filter(recipient=self.request.user)
    def create(self, request, *args, **kwargs):
        # Notifications are only ever created server-side via core.services.notify().
        raise MethodNotAllowed("POST")
    @action(detail=False, methods=["post"])
    def mark_all_read(self, request):
        self.get_queryset().update(is_read=True)
        return Response(status=204)

    @action(detail=False, methods=["get"], url_path="unread-count")
    def unread_count(self, request):
        """
        One number, polled by the shell so the bell in the header can say
        there is something waiting. Deliberately not the notification list:
        this is fetched on a timer from every page.
        """
        return Response({"unread": self.get_queryset().filter(is_read=False).count()})


class AuditLogViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = AuditLogSerializer
    permission_classes = [IsAdmin]
    queryset = AuditLog.objects.select_related("actor", "content_type")
