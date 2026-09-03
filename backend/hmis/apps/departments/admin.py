from django.contrib import admin

from .models import Department, Service


class ServiceInline(admin.TabularInline):
    model = Service
    extra = 0
    fields = ["name", "code", "price", "is_active"]


@admin.register(Department)
class DepartmentAdmin(admin.ModelAdmin):
    list_display = ["name", "code", "manager", "staff_count", "is_active"]
    list_filter = ["is_active"]
    search_fields = ["name", "code"]
    prepopulated_fields = {"code": ("name",)}
    filter_horizontal = ["staff"]
    autocomplete_fields = ["manager"]
    inlines = [ServiceInline]

    @admin.display(description="Staff")
    def staff_count(self, obj):
        return obj.staff.count()


@admin.register(Service)
class ServiceAdmin(admin.ModelAdmin):
    list_display = ["name", "department", "code", "price", "is_active"]
    list_filter = ["department", "is_active"]
    search_fields = ["name", "code"]
