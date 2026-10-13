"""
Core data model for the Smart Asset & Inventory Management System.

Business rules enforced here (so they hold for the admin, the shell, the seed
command and any future API alike):

* An asset's ``status`` is *derived*, not hand-edited. Priority order:
  Retired (manual, sticky) > In repair (open ticket) > Assigned (active
  assignment) > Available.  See ``Asset.sync_status``.
* An asset can have at most one *active* assignment (``date_returned`` empty).
  Enforced by a database constraint.
* A return date can't precede the assigned date. Database constraint.
* Retired assets can't be assigned or repaired, and an asset can only be
  retired once nobody holds it and it has no open tickets.
"""

from django.conf import settings
from django.contrib.auth.models import AbstractUser, UserManager
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import F, Q
from django.utils import timezone


# --------------------------------------------------------------------------- #
# Shared helpers
# --------------------------------------------------------------------------- #

class TimeStampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


def validate_not_in_future(value):
    if value > timezone.localdate():
        raise ValidationError("Purchase date can't be in the future.")


def stored_asset_status(asset_id):
    """The asset's status as it is in the database right now.

    Validation uses this instead of ``record.asset.status`` because an asset
    object held by the caller goes stale: opening a ticket or assigning the
    asset updates the database through a different instance.
    """
    return Asset.objects.filter(pk=asset_id).values_list("status", flat=True).first()


# --------------------------------------------------------------------------- #
# Users & roles
# --------------------------------------------------------------------------- #

class Role(models.TextChoices):
    ADMIN = "admin", "Admin"
    EMPLOYEE = "employee", "Employee"


class SAIMSUserManager(UserManager):
    def create_superuser(self, username, email=None, password=None, **extra_fields):
        # `manage.py createsuperuser` should produce an Admin-role account.
        extra_fields.setdefault("role", Role.ADMIN)
        return super().create_superuser(username, email, password, **extra_fields)


class User(AbstractUser):
    """Django user extended with a role and a few profile fields.

    ``role`` is the single source of truth for access level:

    * Admin    -> ``is_staff`` and ``is_superuser`` are switched on (full access
      to the admin panel).
    * Employee -> both are switched off (can log in to the site, can't enter
      the admin panel).

    The two flags are overwritten from ``role`` on every save, so they can't be
    set independently. The admin form hides them for the same reason.
    """

    role = models.CharField(max_length=20, choices=Role.choices, default=Role.EMPLOYEE, db_index=True)
    department = models.CharField(max_length=100, blank=True)
    phone = models.CharField(max_length=20, blank=True)

    objects = SAIMSUserManager()

    class Meta(AbstractUser.Meta):
        ordering = ["username"]

    def __str__(self):
        full_name = self.get_full_name()
        return f"{full_name} ({self.get_username()})" if full_name else self.get_username()

    @property
    def is_admin_role(self):
        return self.role == Role.ADMIN

    @property
    def is_employee_role(self):
        return self.role == Role.EMPLOYEE

    def save(self, *args, **kwargs):
        is_admin = self.role == Role.ADMIN
        self.is_staff = is_admin
        self.is_superuser = is_admin
        update_fields = kwargs.get("update_fields")
        if update_fields is not None and "role" in update_fields:
            kwargs["update_fields"] = {*update_fields, "is_staff", "is_superuser"}
        super().save(*args, **kwargs)


# --------------------------------------------------------------------------- #
# Assets
# --------------------------------------------------------------------------- #

class Asset(TimeStampedModel):
    class AssetType(models.TextChoices):
        LAPTOP = "laptop", "Laptop"
        DESKTOP = "desktop", "Desktop"
        MONITOR = "monitor", "Monitor"
        PHONE = "phone", "Phone"
        TABLET = "tablet", "Tablet"
        PRINTER = "printer", "Printer"
        FURNITURE = "furniture", "Furniture"
        TOOL = "tool", "Tool"
        OTHER = "other", "Other"

    class Status(models.TextChoices):
        AVAILABLE = "available", "Available"
        ASSIGNED = "assigned", "Assigned"
        IN_REPAIR = "in_repair", "In repair"
        RETIRED = "retired", "Retired"

    name = models.CharField(max_length=150)
    type = models.CharField(max_length=20, choices=AssetType.choices, default=AssetType.OTHER, db_index=True)
    serial_number = models.CharField(max_length=100, unique=True)
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.AVAILABLE,
        db_index=True,
        help_text=(
            "Managed automatically from assignments and repair tickets. "
            "Use the Retire / Reinstate actions on the asset list to take an asset out of service."
        ),
    )
    purchase_date = models.DateField(validators=[validate_not_in_future])

    class Meta:
        ordering = ["name", "serial_number"]

    def __str__(self):
        return f"{self.name} ({self.serial_number})"

    # -- lookups ---------------------------------------------------------------

    def current_assignment(self):
        """The active assignment (employee currently holding the asset), or None."""
        return self.assignments.filter(date_returned__isnull=True).select_related("employee").first()

    def has_open_repair_tickets(self):
        return self.repair_tickets.filter(status__in=RepairTicket.OPEN_STATUSES).exists()

    # -- status handling -------------------------------------------------------

    def sync_status(self):
        """Recompute ``status`` from assignments and repair tickets and store it.

        Reads the stored status from the database (not from this possibly-stale
        instance) so a concurrent retire/reinstate isn't overwritten. Called
        automatically whenever an Assignment or RepairTicket is saved/deleted.
        """
        stored = Asset.objects.filter(pk=self.pk).values_list("status", flat=True).first()
        if stored is None:  # asset no longer exists
            return None

        if stored == self.Status.RETIRED:
            new_status = self.Status.RETIRED
        elif self.has_open_repair_tickets():
            new_status = self.Status.IN_REPAIR
        elif self.assignments.filter(date_returned__isnull=True).exists():
            new_status = self.Status.ASSIGNED
        else:
            new_status = self.Status.AVAILABLE

        if new_status != stored:
            # .update() skips auto_now, so bump updated_at by hand.
            Asset.objects.filter(pk=self.pk).update(status=new_status, updated_at=timezone.now())
        self.status = new_status
        return new_status

    def retire(self):
        """Take the asset out of service. Raises ValidationError if not allowed."""
        self.refresh_from_db(fields=["status"])
        if self.status == self.Status.RETIRED:
            return
        if self.assignments.filter(date_returned__isnull=True).exists():
            raise ValidationError("It is still assigned to someone. Record its return first.")
        if self.has_open_repair_tickets():
            raise ValidationError("It has open repair tickets. Resolve or cancel them first.")
        self.status = self.Status.RETIRED
        self.save(update_fields=["status", "updated_at"])

    def reinstate(self):
        """Bring a retired asset back into circulation."""
        self.refresh_from_db(fields=["status"])
        if self.status != self.Status.RETIRED:
            return
        self.status = self.Status.AVAILABLE
        self.save(update_fields=["status", "updated_at"])
        self.sync_status()


# --------------------------------------------------------------------------- #
# Assignments (asset -> employee, with date range)
# --------------------------------------------------------------------------- #

class Assignment(TimeStampedModel):
    asset = models.ForeignKey(Asset, on_delete=models.PROTECT, related_name="assignments")
    employee = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="assignments")
    date_assigned = models.DateField(default=timezone.localdate)
    date_returned = models.DateField(
        null=True,
        blank=True,
        help_text="Leave empty while the employee still holds the asset.",
    )

    class Meta:
        ordering = ["-date_assigned", "-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["asset"],
                condition=Q(date_returned__isnull=True),
                name="unique_active_assignment_per_asset",
                violation_error_message=(
                    "This asset is already assigned and hasn't been returned yet. "
                    "Record its return before assigning it again."
                ),
            ),
            models.CheckConstraint(
                condition=Q(date_returned__isnull=True) | Q(date_returned__gte=F("date_assigned")),
                name="assignment_return_not_before_assigned",
                violation_error_message="The return date can't be earlier than the assigned date.",
            ),
        ]

    def __str__(self):
        return f"{self.asset} -> {self.employee}"

    @property
    def is_active(self):
        return self.date_returned is None

    def clean(self):
        super().clean()
        errors = {}

        if self.asset_id and self.is_active:
            asset_status = stored_asset_status(self.asset_id)
            if asset_status == Asset.Status.RETIRED:
                errors["asset"] = "Retired assets can't be assigned."
            elif self._state.adding and asset_status == Asset.Status.IN_REPAIR:
                errors["asset"] = "This asset is in repair and can't be assigned until the repair is finished."

        if self._state.adding and self.employee_id and not self.employee.is_active:
            errors["employee"] = "Assets can't be assigned to an inactive user."

        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        # Run the business rules no matter where the save comes from.
        self.full_clean()
        super().save(*args, **kwargs)

    def mark_returned(self, on_date=None):
        """Close the assignment (defaults to today)."""
        if self.date_returned is not None:
            raise ValidationError("This assignment has already been returned.")
        self.date_returned = on_date or timezone.localdate()
        self.save()


# --------------------------------------------------------------------------- #
# Repair tickets
# --------------------------------------------------------------------------- #

class RepairTicket(TimeStampedModel):
    class Status(models.TextChoices):
        OPEN = "open", "Open"
        IN_PROGRESS = "in_progress", "In progress"
        RESOLVED = "resolved", "Resolved"
        CANCELLED = "cancelled", "Cancelled"

    # While a ticket is in one of these states its asset counts as "In repair".
    OPEN_STATUSES = (Status.OPEN, Status.IN_PROGRESS)

    asset = models.ForeignKey(Asset, on_delete=models.PROTECT, related_name="repair_tickets")
    issue = models.TextField(help_text="What is wrong with the asset?")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.OPEN, db_index=True)
    assigned_technician = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="assigned_repairs",
    )
    resolved_at = models.DateTimeField(null=True, blank=True, editable=False)

    class Meta:
        ordering = ["-created_at", "-id"]

    def __str__(self):
        return f"Ticket #{self.pk}: {self.asset.name} ({self.get_status_display()})"

    @property
    def is_open(self):
        return self.status in self.OPEN_STATUSES

    def clean(self):
        super().clean()
        errors = {}

        if self.asset_id and self.is_open and stored_asset_status(self.asset_id) == Asset.Status.RETIRED:
            errors["asset"] = "Retired assets can't have open repair tickets."

        if self._state.adding and self.assigned_technician_id and not self.assigned_technician.is_active:
            errors["assigned_technician"] = "This user is inactive and can't be assigned tickets."

        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        # resolved_at tracks "when did it get resolved" and is cleared on reopen/cancel.
        if self.status == self.Status.RESOLVED:
            if self.resolved_at is None:
                self.resolved_at = timezone.now()
        else:
            self.resolved_at = None
        update_fields = kwargs.get("update_fields")
        if update_fields is not None and "status" in update_fields:
            kwargs["update_fields"] = {*update_fields, "resolved_at"}

        self.full_clean()
        super().save(*args, **kwargs)


# --------------------------------------------------------------------------- #
# Inventory (consumables, cables, peripherals ...: tracked by quantity)
# --------------------------------------------------------------------------- #

class InventoryItemQuerySet(models.QuerySet):
    def low_stock(self):
        """Items at or below their reorder threshold (includes out-of-stock)."""
        return self.filter(quantity__lte=F("threshold"))


class InventoryItem(TimeStampedModel):
    class ItemType(models.TextChoices):
        PRINTER = "printer", "Printer"
        CABLE = "cable", "Cable"
        CONSUMABLE = "consumable", "Consumable"
        PERIPHERAL = "peripheral", "Peripheral"
        ACCESSORY = "accessory", "Accessory"
        OTHER = "other", "Other"

    name = models.CharField(max_length=150, unique=True)
    item_type = models.CharField(max_length=20, choices=ItemType.choices, default=ItemType.OTHER, db_index=True)
    quantity = models.PositiveIntegerField(default=0)
    threshold = models.PositiveIntegerField(
        default=5,
        help_text="Flag the item as low stock when the quantity is at or below this number.",
    )

    objects = InventoryItemQuerySet.as_manager()

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name

    @property
    def is_low_stock(self):
        return self.quantity <= self.threshold
