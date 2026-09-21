
from datetime import timedelta

from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_exempt

from rest_framework import status, viewsets
from rest_framework.exceptions import ValidationError
from rest_framework.authtoken.views import ObtainAuthToken
from rest_framework.authtoken.models import Token
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework.filters import SearchFilter

from apps.core.services import audit_event
from . import lockout
from .models import User
from .permissions import IsAdmin
from .serializers import UserSerializer, UserAdminSerializer, UserDirectorySerializer


def cooldown_minutes(seconds):
    """The cooldown as the message says it. A deployment may shorten the
    setting; the sentence should follow rather than keep saying five."""
    return seconds / 60


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

    **Four failed attempts are allowed; the fifth locks this client out of
    this username for five minutes.** The rule itself lives in
    `accounts/lockout.py` — this view only asks it three questions: may this
    attempt run, that one failed, that one worked. Authentication is unchanged:
    still `ObtainAuthToken` with `AuthTokenSerializer`, still the same 400 and
    the same wording for a wrong password, so nothing here says whether a
    username exists.

    A lockout is **server-side and complete**: it is refused before the
    credentials are so much as looked at, so no amount of client-side
    persuasion gets past it. The countdown the login page shows is drawn from
    `retry_after` below and is feedback, never the control.

    Nothing else in the HMIS is rate limited. This is the only view that counts
    anything, and an authenticated request never touches the lockout at all.
    """

    def _locked(self, request, seconds, username):
        """
        429 with everything the login page needs to explain itself: the
        sentence, a machine-readable `code`, and the seconds left so it can
        count down without guessing. `Retry-After` is the HTTP way of saying
        the same thing, for anything that is not our own frontend.
        """
        minutes = max(1, round(cooldown_minutes(seconds)))
        body = {
            "code": "login_locked",
            "detail": (
                "Too many failed login attempts. For your security, login has been "
                f"temporarily blocked for {minutes} minutes. Please try again after "
                "the lockout expires."
            ),
            "retry_after": seconds,
            "locked_until": (timezone.now() + timedelta(seconds=seconds)).isoformat(),
        }
        response = Response(body, status=status.HTTP_429_TOO_MANY_REQUESTS)
        response["Retry-After"] = str(seconds)
        return response

    def post(self, request, *args, **kwargs):
        username = request.data.get("username", "")

        # Asked before the credentials are read at all: a locked client is
        # refused whether or not it has since guessed the right password.
        locked_for = lockout.remaining(request, username)
        if locked_for:
            return self._locked(request, locked_for, username)

        serializer = self.serializer_class(
            data=request.data,
            context={"request": request},
        )

        try:
            serializer.is_valid(raise_exception=True)
        except ValidationError:
            triggered = lockout.record_failure(request, username)
            if not triggered:
                # The ordinary refusal, unchanged — and deliberately identical
                # for a username that exists and one that does not.
                raise
            # The audit row names the attempt, never whether the account is
            # real: `username` here is whatever was typed.
            audit_event(
                actor=None, action="auth.login_locked", request=request,
                details={"username": str(username)[:150],
                         "client": lockout.client_ip(request),
                         "seconds": triggered},
            )
            return self._locked(request, triggered, username)

        user = serializer.validated_data["user"]

        # Signing in forgets every failure before it.
        lockout.reset(request, username)

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
    filter_backends = [DjangoFilterBackend, SearchFilter]
    filterset_fields = ["role"]
    # Found by the number on the badge (`NMHS-S000001`) as readily as by name,
    # username or email. Searching narrows the directory; what each role is
    # then shown of a row is still the serializer's decision.
    search_fields = ["staff_number", "first_name", "last_name", "username", "email"]

    def get_permissions(self):
        if self.action in ("list", "retrieve"):
            return [IsAuthenticated()]
        return [IsAdmin()]

    def get_serializer_class(self):
        if self.action in ("list", "retrieve") and not getattr(self.request.user, "is_admin", False):
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

