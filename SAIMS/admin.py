from django.contrib import admin, messages
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.forms import AdminUserCreationForm, UserChangeForm
from django.core.exceptions import ValidationError
from django.db.models import F, Prefetch
from django.utils.text import Truncator

from .models import Asset, Assignment, InventoryItem, RepairTicket, User

admin.site.site_header = "Smart AIMS administration"
admin.site.site_title = "Smart AIMS admin"
admin.site.index_title = "Assets, inventory and repairs"


def _short(text, length=60):
    return Truncator(text).chars(length)


class NoRelatedDeleteMixin:
    """Hide the X icon next to foreign-key fields.

    By default it lets you delete the selected asset/user from inside an
    unrelated form (PROTECT only stops that when history exists).
    """

    def formfield_for_dbfield(self, db_field, request, **kwargs):
        formfield = super().formfield_for_dbfield(db_field, request, **kwargs)
        widget = getattr(formfield, "widget", None)
        if widget is not None and hasattr(widget, "can_delete_related"):
            widget.can_delete_related = False
        return formfield


# --------------------------------------------------------------------------- #
# Users
# --------------------------------------------------------------------------- #

class SAIMSUserCreationForm(AdminUserCreationForm):
    class Meta(AdminUserCreationForm.Meta):
        model = User
        fields = ("username", "first_name", "last_name", "email", "phone", "department", "role")


class SAIMSUserChangeForm(UserChangeForm):
    class Meta(UserChangeForm.Meta):
        model = User


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    """Staff/superuser flags follow the role (see ``User.save``), so they're not editable here."""

    form = SAIMSUserChangeForm
    add_form = SAIMSUserCreationForm

    list_display = ("username", "full_name", "email", "role", "department", "is_active")
    list_filter = ("role", "department", "is_active")
    search_fields = ("username", "first_name", "last_name", "email", "department")
    ordering = ("username",)
    readonly_fields = ("last_login", "date_joined")

    fieldsets = (
        (None, {"fields": ("username", "password")}),
        ("Personal info", {"fields": ("first_name", "last_name", "email", "phone", "department")}),
        (
            "Access",
            {
                "fields": ("role", "is_active"),
                "description": "Admins get full access to this admin panel. Employees can log in but can't open it.",
            },
        ),
        ("Important dates", {"fields": ("last_login", "date_joined")}),
    )
    add_fieldsets = (
        (None, {"classes": ("wide",), "fields": ("username", "usable_password", "password1", "password2")}),
        (
            "Profile",
            {"classes": ("wide",), "fields": ("first_name", "last_name", "email", "phone", "department", "role")},
        ),
    )

    @admin.display(description="Name", ordering="first_name")
    def full_name(self, obj):
        return obj.get_full_name() or "-"


# --------------------------------------------------------------------------- #
# Assets
# --------------------------------------------------------------------------- #

class AssignmentInline(admin.TabularInline):
    """Read-only history on the asset page. Create/return assignments from the Assignments admin."""

    model = Assignment
    extra = 0
    can_delete = False
    show_change_link = True
    verbose_name_plural = "Assignment history"
    fields = ("employee", "date_assigned", "date_returned")
    readonly_fields = fields

    def has_add_permission(self, request, obj=None):
        return False


class RepairTicketInline(admin.TabularInline):
    model = RepairTicket
    extra = 0
    can_delete = False
    show_change_link = True
    verbose_name_plural = "Repair history"
    fields = ("ticket", "short_issue", "status", "assigned_technician", "created_at")
    readonly_fields = fields

    def has_add_permission(self, request, obj=None):
        return False

    @admin.display(description="Ticket")
    def ticket(self, obj):
        return f"#{obj.pk}"

    @admin.display(description="Issue")
    def short_issue(self, obj):
        return _short(obj.issue)


@admin.register(Asset)
class AssetAdmin(admin.ModelAdmin):
    list_display = ("name", "type", "serial_number", "status", "current_holder", "purchase_date")
    list_filter = ("status", "type")
    search_fields = ("name", "serial_number")
    ordering = ("name", "serial_number")
    date_hierarchy = "purchase_date"
    # Status is derived from assignments/tickets, so it's display-only here.
    readonly_fields = ("status", "created_at", "updated_at")
    inlines = [AssignmentInline, RepairTicketInline]
    actions = ["retire_assets", "reinstate_assets"]

    def get_queryset(self, request):
        # One extra query for all rows instead of one per row (the "Assigned to" column).
        active_assignments = Prefetch(
            "assignments",
            queryset=Assignment.objects.filter(date_returned__isnull=True).select_related("employee"),
            to_attr="active_assignments",
        )
        return super().get_queryset(request).prefetch_related(active_assignments)

    def get_fields(self, request, obj=None):
        fields = ["name", "type", "serial_number", "purchase_date"]
        if obj is not None:
            fields += ["status", "created_at", "updated_at"]
        return fields

    @admin.display(description="Assigned to")
    def current_holder(self, obj):
        active = getattr(obj, "active_assignments", None)
        if active is None:
            assignment = obj.current_assignment()
        else:
            assignment = active[0] if active else None
        return assignment.employee if assignment else "-"

    @admin.action(description="Retire selected assets")
    def retire_assets(self, request, queryset):
        retired = 0
        for asset in queryset:
            try:
                asset.retire()
                retired += 1
            except ValidationError as exc:
                self.message_user(request, f"{asset.name} ({asset.serial_number}): {' '.join(exc.messages)}", messages.WARNING)
        if retired:
            self.message_user(request, f"Retired {retired} asset(s).", messages.SUCCESS)

    @admin.action(description="Reinstate selected retired assets")
    def reinstate_assets(self, request, queryset):
        reinstated = 0
        for asset in queryset.filter(status=Asset.Status.RETIRED):
            asset.reinstate()
            reinstated += 1
        if reinstated:
            self.message_user(request, f"Reinstated {reinstated} asset(s).", messages.SUCCESS)
        else:
            self.message_user(request, "None of the selected assets were retired.", messages.INFO)


# --------------------------------------------------------------------------- #
# Assignments
# --------------------------------------------------------------------------- #

class AssignmentStateFilter(admin.SimpleListFilter):
    title = "state"
    parameter_name = "state"

    def lookups(self, request, model_admin):
        return [("active", "Currently assigned"), ("returned", "Returned")]

    def queryset(self, request, queryset):
        if self.value() == "active":
            return queryset.filter(date_returned__isnull=True)
        if self.value() == "returned":
            return queryset.filter(date_returned__isnull=False)
        return queryset


@admin.register(Assignment)
class AssignmentAdmin(NoRelatedDeleteMixin, admin.ModelAdmin):
    list_display = ("asset", "employee", "date_assigned", "date_returned", "currently_assigned")
    list_filter = (AssignmentStateFilter, "asset__type", "employee__department")
    search_fields = (
        "asset__name",
        "asset__serial_number",
        "employee__username",
        "employee__first_name",
        "employee__last_name",
    )
    ordering = ("-date_assigned", "-id")
    date_hierarchy = "date_assigned"
    list_select_related = ("asset", "employee")
    autocomplete_fields = ("employee",)
    actions = ["mark_returned_today"]

    def get_fields(self, request, obj=None):
        fields = ["asset", "employee", "date_assigned", "date_returned"]
        if obj is not None:
            fields += ["created_at", "updated_at"]
        return fields

    def get_readonly_fields(self, request, obj=None):
        # Who got which asset is fixed once recorded; record a return instead of editing history.
        return ("asset", "employee", "created_at", "updated_at") if obj else ()

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == "asset":
            # Only assets that can actually be handed out right now.
            kwargs["queryset"] = Asset.objects.filter(status=Asset.Status.AVAILABLE).order_by("name", "serial_number")
        return super().formfield_for_foreignkey(db_field, request, **kwargs)

    @admin.display(boolean=True, description="Active", ordering="date_returned")
    def currently_assigned(self, obj):
        return obj.is_active

    @admin.action(description="Mark selected assignments as returned (today)")
    def mark_returned_today(self, request, queryset):
        returned = 0
        for assignment in queryset.filter(date_returned__isnull=True).select_related("asset", "employee"):
            try:
                assignment.mark_returned()
                returned += 1
            except ValidationError as exc:
                self.message_user(request, f"{assignment}: {' '.join(exc.messages)}", messages.WARNING)
        if returned:
            self.message_user(request, f"Marked {returned} assignment(s) as returned.", messages.SUCCESS)
        else:
            self.message_user(request, "No active assignments were selected.", messages.INFO)


# --------------------------------------------------------------------------- #
# Repair tickets
# --------------------------------------------------------------------------- #

@admin.register(RepairTicket)
class RepairTicketAdmin(NoRelatedDeleteMixin, admin.ModelAdmin):
    list_display = ("ticket_id", "asset", "short_issue", "status", "assigned_technician", "created_at", "resolved_at")
    list_filter = ("status", ("assigned_technician", admin.RelatedOnlyFieldListFilter), "asset__type")
    search_fields = (
        "issue",
        "asset__name",
        "asset__serial_number",
        "assigned_technician__username",
        "assigned_technician__first_name",
        "assigned_technician__last_name",
    )
    ordering = ("-created_at", "-id")
    date_hierarchy = "created_at"
    list_select_related = ("asset", "assigned_technician")
    autocomplete_fields = ("asset", "assigned_technician")
    actions = ["mark_in_progress", "mark_resolved"]

    def get_fields(self, request, obj=None):
        fields = ["asset", "issue", "status", "assigned_technician"]
        if obj is not None:
            fields += ["resolved_at", "created_at", "updated_at"]
        return fields

    def get_readonly_fields(self, request, obj=None):
        return ("asset", "resolved_at", "created_at", "updated_at") if obj else ()

    @admin.display(description="Ticket", ordering="id")
    def ticket_id(self, obj):
        return f"#{obj.pk}"

    @admin.display(description="Issue")
    def short_issue(self, obj):
        return _short(obj.issue)

    def _bulk_set_status(self, request, queryset, new_status, label):
        changed = 0
        # Save one by one (not queryset.update) so timestamps and asset status stay correct.
        for ticket in queryset.exclude(status=new_status).select_related("asset"):
            ticket.status = new_status
            try:
                ticket.save()
                changed += 1
            except ValidationError as exc:
                self.message_user(request, f"Ticket #{ticket.pk}: {' '.join(exc.messages)}", messages.WARNING)
        if changed:
            self.message_user(request, f"Marked {changed} ticket(s) as {label}.", messages.SUCCESS)

    @admin.action(description="Mark selected tickets as in progress")
    def mark_in_progress(self, request, queryset):
        self._bulk_set_status(request, queryset, RepairTicket.Status.IN_PROGRESS, "in progress")

    @admin.action(description="Mark selected tickets as resolved")
    def mark_resolved(self, request, queryset):
        self._bulk_set_status(request, queryset, RepairTicket.Status.RESOLVED, "resolved")


# --------------------------------------------------------------------------- #
# Inventory
# --------------------------------------------------------------------------- #

class StockLevelFilter(admin.SimpleListFilter):
    title = "stock level"
    parameter_name = "stock"

    def lookups(self, request, model_admin):
        return [
            ("low", "Low (at or below threshold)"),
            ("out", "Out of stock"),
            ("ok", "OK"),
        ]

    def queryset(self, request, queryset):
        if self.value() == "low":
            return queryset.filter(quantity__lte=F("threshold"))
        if self.value() == "out":
            return queryset.filter(quantity=0)
        if self.value() == "ok":
            return queryset.filter(quantity__gt=F("threshold"))
        return queryset


@admin.register(InventoryItem)
class InventoryItemAdmin(admin.ModelAdmin):
    list_display = ("name", "item_type", "quantity", "threshold", "stock_ok")
    list_editable = ("quantity", "threshold")  # quick stock-takes straight from the list
    list_filter = ("item_type", StockLevelFilter)
    search_fields = ("name",)
    ordering = ("name",)

    def get_fields(self, request, obj=None):
        fields = ["name", "item_type", "quantity", "threshold"]
        return fields + ["created_at", "updated_at"] if obj is not None else fields

    def get_readonly_fields(self, request, obj=None):
        return ("created_at", "updated_at") if obj else ()

    @admin.display(boolean=True, description="Stock OK")
    def stock_ok(self, obj):
        return not obj.is_low_stock
