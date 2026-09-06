from django.contrib import admin

from .models import Prescription


@admin.register(Prescription)
class PrescriptionAdmin(admin.ModelAdmin):
    """
    Dispensing deducts stock and raises a charge inside one transaction
    (pharmacy/services.py). Flipping `status` to "dispensed" here does none
    of that — it only makes the record lie. Dispense from the Pharmacy
    counter; this list is for looking things up.
    """
    list_display = ["created_at", "patient", "item", "quantity", "status", "doctor", "dispensed_by"]
    list_filter = ["status", "created_at"]
    search_fields = ["patient__first_name", "patient__last_name", "patient__patient_number", "item__name"]
    list_select_related = ["patient", "item", "doctor", "dispensed_by"]
    autocomplete_fields = ["patient", "item", "doctor", "dispensed_by"]
    date_hierarchy = "created_at"
    list_per_page = 50
    readonly_fields = ["dispensed_at", "dispensed_value"]
