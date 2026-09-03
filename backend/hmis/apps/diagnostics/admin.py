from django.contrib import admin

from .models import InvestigationCatalog, InvestigationOrder, InvestigationResult


@admin.register(InvestigationCatalog)
class InvestigationCatalogAdmin(admin.ModelAdmin):
    list_display = ["name", "code", "kind", "department", "price", "is_active"]
    list_filter = ["kind", "is_active", "department"]
    search_fields = ["name", "code"]
    prepopulated_fields = {"code": ("name",)}


class InvestigationResultInline(admin.StackedInline):
    model = InvestigationResult
    extra = 0
    fields = ["findings", "conclusion", "values", "attachment", "released_by"]


@admin.register(InvestigationOrder)
class InvestigationOrderAdmin(admin.ModelAdmin):
    list_display = ["created_at", "patient", "investigation", "status", "requested_by", "performed_by"]
    list_filter = ["status", "created_at", "investigation__kind"]
    search_fields = ["patient__first_name", "patient__last_name", "patient__file_number",
                     "investigation__name"]
    list_select_related = ["patient", "investigation", "requested_by", "performed_by"]
    autocomplete_fields = ["patient", "requested_by", "performed_by"]
    date_hierarchy = "created_at"
    list_per_page = 50
    inlines = [InvestigationResultInline]


@admin.register(InvestigationResult)
class InvestigationResultAdmin(admin.ModelAdmin):
    list_display = ["order", "released_at", "released_by"]
    search_fields = ["order__patient__first_name", "order__patient__last_name", "findings"]
    list_select_related = ["order__patient", "released_by"]
