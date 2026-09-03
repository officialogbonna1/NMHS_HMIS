from django.contrib.contenttypes.models import ContentType
from .models import AuditLog, Notification


def audit_event(*, actor, action, instance=None, details=None, request=None):
    data = {"actor": actor, "action": action, "details": details or {}}
    if instance is not None and instance.pk:
        data.update(content_type=ContentType.objects.get_for_model(instance), object_id=instance.pk)
    if request:
        data["ip_address"] = request.META.get("REMOTE_ADDR")
    return AuditLog.objects.create(**data)


def notify(*, recipient, title, message="", category="general", action_url=""):
    return Notification.objects.create(recipient=recipient, title=title, message=message,
                                      category=category, action_url=action_url)
