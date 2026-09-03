
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_exempt

from rest_framework import viewsets
from rest_framework.authtoken.views import ObtainAuthToken
from rest_framework.authtoken.models import Token
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated
from django_filters.rest_framework import DjangoFilterBackend

from apps.core.services import audit_event
from .models import User
from .permissions import IsAdmin
from .serializers import UserSerializer, UserAdminSerializer, UserDirectorySerializer


@method_decorator(csrf_exempt, name="dispatch")
class LoginView(ObtainAuthToken):
    """
    POST /api/auth/login/

    {
        "username": "...",
        "password": "..."
    }

    Returns:

    {
        "token": "...",
        "user": {...}
    }
    """

    def post(self, request, *args, **kwargs):
        serializer = self.serializer_class(
            data=request.data,
            context={"request": request},
        )

        serializer.is_valid(raise_exception=True)

        user = serializer.validated_data["user"]

        token, _ = Token.objects.get_or_create(user=user)

        return Response({
            "token": token.key,
            "user": UserSerializer(user).data,
        })


class MeView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response(
            UserSerializer(request.user).data
        )


class LogoutView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        if hasattr(request.user, "auth_token"):
            request.user.auth_token.delete()

        return Response(status=204)


class UserViewSet(viewsets.ModelViewSet):
    """Account management. Create/edit/disable/reset-password stay
    admin-only (accounts.permissions.IsAdmin). list/retrieve are open to
    any authenticated user — e.g. picking a doctor when booking an
    appointment — but a non-admin caller gets UserDirectorySerializer's
    minimal fields, never the admin view's email/department/activity."""

    queryset = User.objects.all().order_by("username")
    filter_backends = [DjangoFilterBackend]
    filterset_fields = ["role"]

    def get_permissions(self):
        if self.action in ("list", "retrieve"):
            return [IsAuthenticated()]
        return [IsAdmin()]

    def get_serializer_class(self):
        if self.action in ("list", "retrieve") and not self.request.user.is_admin:
            return UserDirectorySerializer
        return UserAdminSerializer

    def perform_create(self, serializer):
        user = serializer.save()
        audit_event(actor=self.request.user, action="user.created", instance=user, request=self.request)

    def perform_update(self, serializer):
        user = serializer.save()
        audit_event(actor=self.request.user, action="user.updated", instance=user, request=self.request)

    @action(detail=True, methods=["post"])
    def set_password(self, request, pk=None):
        user = self.get_object()
        password = request.data.get("password")
        if not password:
            return Response({"password": "Required."}, status=400)
        user.set_password(password)
        user.must_change_password = request.data.get("must_change_password", True)
        user.save(update_fields=["password", "must_change_password"])
        # Force re-authentication elsewhere: a changed password should not
        # leave old sessions valid.
        Token.objects.filter(user=user).delete()
        audit_event(actor=request.user, action="user.password_reset", instance=user, request=request)
        return Response(status=204)

