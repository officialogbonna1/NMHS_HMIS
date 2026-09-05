from django.contrib.contenttypes.models import ContentType

from .models import AuditLog, Notification, NotificationSetting

# A result coming back to the doctor who ordered it is not a preference, so
# this category is not switchable off: `notify()` ignores a setting that
# tries to. Everything else the hospital may quieten.
ALWAYS_ON = {"clinical"}


def audit_event(*, actor, action, instance=None, details=None, request=None):
    data = {"actor": actor, "action": action, "details": details or {}}
    if instance is not None and instance.pk:
        data.update(content_type=ContentType.objects.get_for_model(instance), object_id=instance.pk)
    if request:
        data["ip_address"] = request.META.get("REMOTE_ADDR")
    return AuditLog.objects.create(**data)


def category_is_enabled(category):
    """
    Is this kind of notification switched on?

    Unknown and unconfigured categories are on — the default has to be
    "deliver it", or adding a new kind of notification would silently send
    nothing until somebody remembered to add a row.
    """
    if category in ALWAYS_ON:
        return True
    setting = NotificationSetting.objects.filter(category=category).first()
    return True if setting is None else setting.is_enabled


def notify(*, recipient, title, message="", category="general", action_url=""):
    """
    Raise a notification, unless the hospital has switched this category off.

    The check lives here rather than at each of the ~20 call sites, so a
    category cannot be half-disabled: one place decides, and
    `NotificationSetting` is what it reads.
    """
    if not category_is_enabled(category):
        return None
    return Notification.objects.create(recipient=recipient, title=title, message=message,
                                      category=category, action_url=action_url)
