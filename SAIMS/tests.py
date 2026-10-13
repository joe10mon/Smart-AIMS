"""
Run with:  python manage.py test

Needs DJANGO_SECRET_KEY to be available (via .env or the environment), like any
other management command.
"""

import io
import itertools
import os
import subprocess
import sys
import tempfile
from datetime import timedelta
from pathlib import Path
from unittest import mock

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.management import CommandError, call_command
from django.db import IntegrityError, transaction
from django.db.models import ProtectedError
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from SAIMSadmin.env import env_bool, env_list, load_dotenv

from .models import Asset, Assignment, InventoryItem, RepairTicket, Role, User

PASSWORD = "Tr1cky-Passw0rd!"
_serials = itertools.count(1)


@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class BaseTestCase(TestCase):
    """Production password hashing is deliberately slow; tests don't need that cost."""


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def make_user(username="emp", role=Role.EMPLOYEE, **extra):
    return User.objects.create_user(username=username, password=PASSWORD, role=role, **extra)


def make_asset(**extra):
    values = {
        "name": "Test laptop",
        "type": Asset.AssetType.LAPTOP,
        "serial_number": f"SN-{next(_serials):05d}",
        "purchase_date": timezone.localdate() - timedelta(days=30),
    }
    values.update(extra)
    return Asset.objects.create(**values)


def assign(asset, employee, days_ago=5, returned_days_ago=None):
    today = timezone.localdate()
    return Assignment.objects.create(
        asset=asset,
        employee=employee,
        date_assigned=today - timedelta(days=days_ago),
        date_returned=None if returned_days_ago is None else today - timedelta(days=returned_days_ago),
    )


def open_ticket(asset, **extra):
    return RepairTicket.objects.create(asset=asset, issue=extra.pop("issue", "Something is broken"), **extra)


def status_of(asset):
    return Asset.objects.get(pk=asset.pk).status


def seed():
    call_command("seed_data", "--force", stdout=io.StringIO())


# --------------------------------------------------------------------------- #
# Users and roles
# --------------------------------------------------------------------------- #

class UserRoleTests(BaseTestCase):
    def test_employee_has_no_admin_access(self):
        user = make_user()
        self.assertEqual(user.role, Role.EMPLOYEE)
        self.assertFalse(user.is_staff)
        self.assertFalse(user.is_superuser)

    def test_admin_role_grants_full_access(self):
        user = make_user("boss", role=Role.ADMIN)
        self.assertTrue(user.is_staff)
        self.assertTrue(user.is_superuser)

    def test_createsuperuser_produces_an_admin_role_account(self):
        user = User.objects.create_superuser("root", "root@example.com", PASSWORD)
        self.assertEqual(user.role, Role.ADMIN)
        self.assertTrue(user.is_superuser)

    def test_demoting_an_admin_revokes_access(self):
        user = make_user("boss", role=Role.ADMIN)
        user.role = Role.EMPLOYEE
        user.save()
        user.refresh_from_db()
        self.assertFalse(user.is_staff)
        self.assertFalse(user.is_superuser)

    def test_flags_are_persisted_when_saving_only_the_role(self):
        user = make_user("boss", role=Role.ADMIN)
        user.role = Role.EMPLOYEE
        user.save(update_fields=["role"])
        user.refresh_from_db()
        self.assertFalse(user.is_superuser)

    def test_flags_cannot_be_set_independently_of_the_role(self):
        user = make_user()
        user.is_staff = user.is_superuser = True
        user.save()
        user.refresh_from_db()
        self.assertFalse(user.is_staff)
        self.assertFalse(user.is_superuser)

    def test_str_shows_name_and_username(self):
        self.assertEqual(str(make_user("jd", first_name="Jane", last_name="Doe")), "Jane Doe (jd)")
        self.assertEqual(str(make_user("solo")), "solo")


# --------------------------------------------------------------------------- #
# Asset status is derived from assignments and tickets
# --------------------------------------------------------------------------- #

class AssetStatusTests(BaseTestCase):
    def setUp(self):
        self.employee = make_user()
        self.asset = make_asset()

    def test_new_assets_are_available(self):
        self.assertEqual(self.asset.status, Asset.Status.AVAILABLE)

    def test_assigning_and_returning(self):
        assignment = assign(self.asset, self.employee)
        self.assertEqual(status_of(self.asset), Asset.Status.ASSIGNED)
        assignment.mark_returned()
        self.assertEqual(status_of(self.asset), Asset.Status.AVAILABLE)

    def test_deleting_an_active_assignment_frees_the_asset(self):
        assign(self.asset, self.employee)
        Assignment.objects.filter(asset=self.asset).delete()  # bulk path used by the admin
        self.assertEqual(status_of(self.asset), Asset.Status.AVAILABLE)

    def test_open_ticket_puts_asset_in_repair_and_resolving_restores_it(self):
        assign(self.asset, self.employee)
        ticket = open_ticket(self.asset)
        self.assertEqual(status_of(self.asset), Asset.Status.IN_REPAIR)

        ticket.status = RepairTicket.Status.IN_PROGRESS
        ticket.save()
        self.assertEqual(status_of(self.asset), Asset.Status.IN_REPAIR)

        ticket.status = RepairTicket.Status.RESOLVED
        ticket.save()
        # Still held by the employee, so back to Assigned (not Available).
        self.assertEqual(status_of(self.asset), Asset.Status.ASSIGNED)

    def test_resolving_a_ticket_on_an_unassigned_asset_makes_it_available(self):
        ticket = open_ticket(self.asset)
        self.assertEqual(status_of(self.asset), Asset.Status.IN_REPAIR)
        ticket.status = RepairTicket.Status.CANCELLED
        ticket.save()
        self.assertEqual(status_of(self.asset), Asset.Status.AVAILABLE)

    def test_asset_stays_in_repair_until_every_open_ticket_is_closed(self):
        first, second = open_ticket(self.asset), open_ticket(self.asset)
        first.status = RepairTicket.Status.RESOLVED
        first.save()
        self.assertEqual(status_of(self.asset), Asset.Status.IN_REPAIR)
        second.status = RepairTicket.Status.RESOLVED
        second.save()
        self.assertEqual(status_of(self.asset), Asset.Status.AVAILABLE)

    def test_returning_an_asset_that_is_in_repair_keeps_it_in_repair(self):
        assignment = assign(self.asset, self.employee)
        open_ticket(self.asset)
        assignment.mark_returned()
        self.assertEqual(status_of(self.asset), Asset.Status.IN_REPAIR)

    def test_moving_a_record_to_another_asset_resyncs_both(self):
        other = make_asset()
        assignment = assign(self.asset, self.employee)
        assignment.asset = other
        assignment.save()
        self.assertEqual(status_of(self.asset), Asset.Status.AVAILABLE)
        self.assertEqual(status_of(other), Asset.Status.ASSIGNED)

    def test_retire_and_reinstate(self):
        self.asset.retire()
        self.assertEqual(status_of(self.asset), Asset.Status.RETIRED)
        self.asset.reinstate()
        self.assertEqual(status_of(self.asset), Asset.Status.AVAILABLE)

    def test_cannot_retire_an_asset_someone_holds(self):
        assign(self.asset, self.employee)
        with self.assertRaises(ValidationError):
            self.asset.retire()
        self.assertEqual(status_of(self.asset), Asset.Status.ASSIGNED)

    def test_cannot_retire_an_asset_with_open_tickets(self):
        open_ticket(self.asset)
        with self.assertRaises(ValidationError):
            self.asset.retire()

    def test_retired_status_is_sticky(self):
        old = assign(self.asset, self.employee, days_ago=20, returned_days_ago=10)
        self.asset.retire()
        old.save()  # fires the sync signal; must not revive the asset
        self.assertEqual(status_of(self.asset), Asset.Status.RETIRED)

    def test_sync_uses_the_stored_status_not_a_stale_instance(self):
        stale = Asset.objects.get(pk=self.asset.pk)
        self.asset.retire()
        stale.sync_status()  # `stale` still believes the asset is available
        self.assertEqual(status_of(self.asset), Asset.Status.RETIRED)

    def test_rules_use_the_database_status_not_a_stale_instance(self):
        """Regression: opening a ticket updates the DB via another instance, leaving this one stale."""
        stale = self.asset
        open_ticket(stale)
        self.assertEqual(stale.status, Asset.Status.AVAILABLE)  # in-memory value is out of date
        with self.assertRaises(ValidationError):
            assign(stale, self.employee)

    def test_reinstate_works_from_a_stale_instance(self):
        stale = Asset.objects.get(pk=self.asset.pk)
        self.asset.retire()
        stale.reinstate()
        self.assertEqual(status_of(self.asset), Asset.Status.AVAILABLE)

    def test_purchase_date_cannot_be_in_the_future(self):
        asset = Asset(
            name="Time machine",
            type=Asset.AssetType.OTHER,
            serial_number="FUTURE-1",
            purchase_date=timezone.localdate() + timedelta(days=1),
        )
        with self.assertRaises(ValidationError) as ctx:
            asset.full_clean()
        self.assertIn("purchase_date", ctx.exception.message_dict)

    def test_serial_numbers_are_unique(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            make_asset(serial_number=self.asset.serial_number)


# --------------------------------------------------------------------------- #
# Assignment rules
# --------------------------------------------------------------------------- #

class AssignmentRuleTests(BaseTestCase):
    def setUp(self):
        self.alice = make_user("alice")
        self.bob = make_user("bob")
        self.asset = make_asset()

    def test_an_asset_cannot_have_two_active_assignments(self):
        assign(self.asset, self.alice)
        with self.assertRaises(ValidationError) as ctx:
            assign(self.asset, self.bob)
        self.assertIn("already assigned", " ".join(ctx.exception.messages))

    def test_the_database_also_blocks_two_active_assignments(self):
        """The constraint must hold even for code paths that skip model validation."""
        assign(self.asset, self.alice)
        with self.assertRaises(IntegrityError), transaction.atomic():
            Assignment.objects.bulk_create(
                [Assignment(asset=self.asset, employee=self.bob, date_assigned=timezone.localdate())]
            )

    def test_an_asset_can_be_reassigned_after_it_is_returned(self):
        assign(self.asset, self.alice, days_ago=10).mark_returned()
        assign(self.asset, self.bob, days_ago=0)
        self.assertEqual(self.asset.current_assignment().employee, self.bob)
        self.assertEqual(self.asset.assignments.count(), 2)

    def test_return_date_cannot_precede_assigned_date(self):
        with self.assertRaises(ValidationError) as ctx:
            assign(self.asset, self.alice, days_ago=2, returned_days_ago=5)
        self.assertIn("earlier than the assigned date", " ".join(ctx.exception.messages))

    def test_the_database_also_blocks_a_return_before_the_assigned_date(self):
        today = timezone.localdate()
        with self.assertRaises(IntegrityError), transaction.atomic():
            Assignment.objects.bulk_create(
                [
                    Assignment(
                        asset=self.asset,
                        employee=self.alice,
                        date_assigned=today,
                        date_returned=today - timedelta(days=1),
                    )
                ]
            )

    def test_retired_assets_cannot_be_assigned(self):
        self.asset.retire()
        with self.assertRaises(ValidationError) as ctx:
            assign(self.asset, self.alice)
        self.assertIn("asset", ctx.exception.message_dict)

    def test_assets_in_repair_cannot_be_assigned(self):
        open_ticket(self.asset)
        with self.assertRaises(ValidationError) as ctx:
            assign(self.asset, self.alice)
        self.assertIn("asset", ctx.exception.message_dict)

    def test_inactive_users_cannot_receive_assets(self):
        self.alice.is_active = False
        self.alice.save()
        with self.assertRaises(ValidationError) as ctx:
            assign(self.asset, self.alice)
        self.assertIn("employee", ctx.exception.message_dict)

    def test_offboarding_does_not_block_recording_a_return(self):
        """Deactivating an employee first and returning their laptop afterwards must still work."""
        assignment = assign(self.asset, self.alice)
        self.alice.is_active = False
        self.alice.save()
        assignment.mark_returned()
        self.assertEqual(status_of(self.asset), Asset.Status.AVAILABLE)

    def test_editing_an_active_assignment_while_the_asset_is_in_repair_is_allowed(self):
        assignment = assign(self.asset, self.alice, days_ago=10)
        open_ticket(self.asset)
        assignment.date_assigned = timezone.localdate() - timedelta(days=12)
        assignment.save()  # must not raise

    def test_returning_twice_is_rejected(self):
        assignment = assign(self.asset, self.alice)
        assignment.mark_returned()
        with self.assertRaises(ValidationError):
            assignment.mark_returned()

    def test_history_protects_assets_and_users_from_deletion(self):
        assign(self.asset, self.alice, days_ago=10, returned_days_ago=1)
        with self.assertRaises(ProtectedError):
            self.asset.delete()
        with self.assertRaises(ProtectedError):
            self.alice.delete()


# --------------------------------------------------------------------------- #
# Repair tickets
# --------------------------------------------------------------------------- #

class RepairTicketTests(BaseTestCase):
    def setUp(self):
        self.asset = make_asset()
        self.tech = make_user("tech", department="IT Support")

    def test_new_tickets_default_to_open(self):
        ticket = open_ticket(self.asset, assigned_technician=self.tech)
        self.assertEqual(ticket.status, RepairTicket.Status.OPEN)
        self.assertIsNone(ticket.resolved_at)
        self.assertTrue(ticket.is_open)

    def test_resolved_at_is_set_when_resolved_and_cleared_on_reopen(self):
        ticket = open_ticket(self.asset)
        ticket.status = RepairTicket.Status.RESOLVED
        ticket.save()
        self.assertIsNotNone(ticket.resolved_at)
        stamped = ticket.resolved_at

        ticket.issue = "Edited while resolved"
        ticket.save()
        self.assertEqual(ticket.resolved_at, stamped)  # not re-stamped by unrelated edits

        ticket.status = RepairTicket.Status.IN_PROGRESS
        ticket.save()
        self.assertIsNone(ticket.resolved_at)

    def test_cancelled_tickets_have_no_resolution_time(self):
        ticket = open_ticket(self.asset)
        ticket.status = RepairTicket.Status.CANCELLED
        ticket.save()
        self.assertIsNone(ticket.resolved_at)

    def test_saving_only_the_status_also_persists_resolved_at(self):
        ticket = open_ticket(self.asset)
        ticket.status = RepairTicket.Status.RESOLVED
        ticket.save(update_fields=["status"])
        ticket.refresh_from_db()
        self.assertIsNotNone(ticket.resolved_at)

    def test_retired_assets_cannot_get_open_tickets(self):
        self.asset.retire()
        with self.assertRaises(ValidationError):
            open_ticket(self.asset)

    def test_a_stale_asset_instance_cannot_get_a_ticket_after_retirement(self):
        stale = Asset.objects.get(pk=self.asset.pk)
        self.asset.retire()
        with self.assertRaises(ValidationError):
            open_ticket(stale)

    def test_a_closed_ticket_on_a_retired_asset_is_fine(self):
        """Seed data relies on this: the ticket is closed, then the asset retired."""
        ticket = open_ticket(self.asset)
        ticket.status = RepairTicket.Status.RESOLVED
        ticket.save()
        self.asset.retire()
        ticket.issue = "Edited after retirement"
        ticket.save()  # must not raise

    def test_inactive_technicians_cannot_be_given_new_tickets(self):
        self.tech.is_active = False
        self.tech.save()
        with self.assertRaises(ValidationError):
            open_ticket(self.asset, assigned_technician=self.tech)

    def test_deleting_a_technician_keeps_the_ticket(self):
        ticket = open_ticket(self.asset, assigned_technician=self.tech)
        self.tech.delete()
        ticket.refresh_from_db()
        self.assertIsNone(ticket.assigned_technician)

    def test_tickets_protect_assets_from_deletion(self):
        open_ticket(self.asset)
        with self.assertRaises(ProtectedError):
            self.asset.delete()


# --------------------------------------------------------------------------- #
# Inventory
# --------------------------------------------------------------------------- #

class InventoryTests(BaseTestCase):
    def test_low_stock_includes_the_threshold_boundary_and_out_of_stock(self):
        InventoryItem.objects.create(name="Plenty", quantity=11, threshold=10)
        InventoryItem.objects.create(name="At threshold", quantity=10, threshold=10)
        InventoryItem.objects.create(name="Below", quantity=3, threshold=10)
        InventoryItem.objects.create(name="Out", quantity=0, threshold=10)
        InventoryItem.objects.create(name="Zero threshold, empty", quantity=0, threshold=0)
        low = set(InventoryItem.objects.low_stock().values_list("name", flat=True))
        self.assertEqual(low, {"At threshold", "Below", "Out", "Zero threshold, empty"})

    def test_is_low_stock_matches_the_queryset(self):
        for name, quantity in [("a", 4), ("b", 5), ("c", 6)]:
            InventoryItem.objects.create(name=name, quantity=quantity, threshold=5)
        flagged = {item.name for item in InventoryItem.objects.all() if item.is_low_stock}
        self.assertEqual(flagged, set(InventoryItem.objects.low_stock().values_list("name", flat=True)))

    def test_quantities_cannot_be_negative(self):
        item = InventoryItem(name="Negative", quantity=-1, threshold=1)
        with self.assertRaises(ValidationError):
            item.full_clean()

    def test_names_are_unique(self):
        InventoryItem.objects.create(name="HDMI cable")
        with self.assertRaises(IntegrityError), transaction.atomic():
            InventoryItem.objects.create(name="HDMI cable")


# --------------------------------------------------------------------------- #
# Admin panel (runs against the seeded demo data)
# --------------------------------------------------------------------------- #

class AdminTests(BaseTestCase):
    @classmethod
    def setUpTestData(cls):
        seed()
        cls.admin_user = User.objects.get(username="admin")

    def setUp(self):
        self.client.force_login(self.admin_user)

    def url(self, model, view, *args):
        return reverse(f"admin:SAIMS_{model}_{view}", args=args)

    def changelist(self, model, **params):
        response = self.client.get(self.url(model, "changelist"), params)
        self.assertEqual(response.status_code, 200, f"{model} changelist {params}")
        return response

    # -- pages render ----------------------------------------------------------

    def test_every_list_add_and_change_page_renders(self):
        objects = {
            "user": User.objects.get(username="priya.nair"),
            "asset": Asset.objects.get(serial_number="DL5440-A1002"),
            "assignment": Assignment.objects.filter(date_returned__isnull=True).first(),
            "repairticket": RepairTicket.objects.first(),
            "inventoryitem": InventoryItem.objects.first(),
        }
        for model, obj in objects.items():
            with self.subTest(model=model):
                self.changelist(model)
                self.assertEqual(self.client.get(self.url(model, "add")).status_code, 200)
                self.assertEqual(self.client.get(self.url(model, "change", obj.pk)).status_code, 200)

    def test_admin_index_lists_all_models(self):
        response = self.client.get(reverse("admin:index"))
        for label in ["Assets", "Assignments", "Repair tickets", "Inventory items", "Users"]:
            self.assertContains(response, label)

    def test_date_hierarchy_pages_work(self):
        year = timezone.localdate().year
        self.changelist("repairticket", created_at__year=year)
        self.changelist("assignment", date_assigned__year=year)
        self.changelist("asset", purchase_date__year=year)

    # -- search ----------------------------------------------------------------

    def test_search(self):
        cases = [
            ("asset", "ThinkPad", 2),
            ("asset", "DL5440", 3),
            ("user", "priya", 1),
            ("assignment", "priya", 3),  # her laptop, monitor and chair
            ("repairticket", "battery", 1),
            ("repairticket", "mathew", 4),  # tickets assigned to John Mathew
            ("inventoryitem", "toner", 2),
        ]
        for model, query, expected in cases:
            with self.subTest(model=model, query=query):
                self.assertEqual(self.changelist(model, q=query).context["cl"].result_count, expected)

    # -- filters ---------------------------------------------------------------

    def test_filters(self):
        cases = [
            ("asset", {"status__exact": "in_repair"}, 4),
            ("asset", {"status__exact": "retired"}, 2),
            ("asset", {"type__exact": "laptop"}, 8),
            ("assignment", {"state": "active"}, 15),
            ("assignment", {"state": "returned"}, 8),
            ("assignment", {"asset__type__exact": "monitor"}, 4),  # 5 monitors, one never assigned
            ("repairticket", {"status__exact": "open"}, 2),
            ("repairticket", {"status__exact": "resolved"}, 5),
            ("inventoryitem", {"stock": "low"}, 5),
            ("inventoryitem", {"stock": "out"}, 1),
            ("inventoryitem", {"stock": "ok"}, 10),
            ("inventoryitem", {"item_type__exact": "cable"}, 4),
            ("user", {"role__exact": "admin"}, 1),
            ("user", {"department": "IT Support"}, 3),  # two technicians + the admin
        ]
        for model, params, expected in cases:
            with self.subTest(model=model, params=params):
                self.assertEqual(self.changelist(model, **params).context["cl"].result_count, expected)

    def test_filter_by_technician_only_offers_people_with_tickets(self):
        response = self.changelist("repairticket")
        technicians = {u.username for u in User.objects.filter(assigned_repairs__isnull=False).distinct()}
        self.assertEqual(technicians, {"john.mathew", "arun.kumar"})
        self.assertContains(response, "Mathew")
        self.assertNotContains(response, "priya.nair")

    # -- ordering --------------------------------------------------------------

    def test_default_and_custom_ordering(self):
        names = [a.name for a in self.changelist("asset").context["cl"].result_list]
        self.assertEqual(names, sorted(names))

        newest_first = list(self.changelist("assignment").context["cl"].result_list)
        dates = [a.date_assigned for a in newest_first]
        self.assertEqual(dates, sorted(dates, reverse=True))

        tickets = list(self.changelist("repairticket").context["cl"].result_list)
        created = [t.created_at for t in tickets]
        self.assertEqual(created, sorted(created, reverse=True))

        # Clicking a column header sorts by it: column 2 of the asset list is "type".
        types = [a.type for a in self.changelist("asset", o="2").context["cl"].result_list]
        self.assertEqual(types, sorted(types))

    # -- derived/locked fields -------------------------------------------------

    def test_asset_status_is_read_only_in_the_form(self):
        asset = Asset.objects.get(serial_number="DL5440-A1002")
        response = self.client.get(self.url("asset", "change", asset.pk))
        self.assertNotContains(response, 'name="status"')
        self.assertContains(response, "In repair")  # shown, just not editable

    def test_asset_page_shows_assignment_and_repair_history(self):
        asset = Asset.objects.get(serial_number="TP-T14-PF3A71")  # has a previous holder
        response = self.client.get(self.url("asset", "change", asset.pk))
        self.assertContains(response, "Assignment history")
        self.assertContains(response, "Arun Kumar")
        self.assertContains(response, "Anita Joseph")

    def test_related_fields_cannot_delete_the_selected_record(self):
        """The default pencil/X/eye icons next to a foreign key let you delete the chosen asset or user."""
        for model in ("assignment", "repairticket"):
            with self.subTest(model=model):
                response = self.client.get(self.url(model, "add"))
                self.assertNotContains(response, "delete-related")
                self.assertContains(response, "add-related")  # adding a new one inline is still useful

    def test_assignment_asset_and_employee_are_locked_after_creation(self):
        assignment = Assignment.objects.filter(date_returned__isnull=True).first()
        response = self.client.get(self.url("assignment", "change", assignment.pk))
        self.assertNotContains(response, 'name="asset"')
        self.assertNotContains(response, 'name="employee"')
        self.assertContains(response, 'name="date_returned"')

    # -- creating records through the admin forms ------------------------------

    def test_create_assignment_for_an_available_asset(self):
        asset = Asset.objects.get(serial_number="DU2723-9003")
        employee = User.objects.get(username="emily.brown")
        response = self.client.post(
            self.url("assignment", "add"),
            {"asset": asset.pk, "employee": employee.pk, "date_assigned": "2026-01-15", "date_returned": ""},
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(status_of(asset), Asset.Status.ASSIGNED)

    def test_assignment_form_only_offers_available_assets(self):
        response = self.client.get(self.url("assignment", "add"))
        offered = {a.pk for a in response.context["adminform"].form.fields["asset"].queryset}
        self.assertEqual(offered, set(Asset.objects.filter(status=Asset.Status.AVAILABLE).values_list("pk", flat=True)))

    def test_cannot_create_an_assignment_for_an_asset_that_is_already_assigned(self):
        asset = Asset.objects.get(serial_number="DL5440-A1001")  # assigned to priya
        before = Assignment.objects.count()
        response = self.client.post(
            self.url("assignment", "add"),
            {
                "asset": asset.pk,
                "employee": User.objects.get(username="emily.brown").pk,
                "date_assigned": "2026-01-15",
                "date_returned": "",
            },
        )
        self.assertEqual(response.status_code, 200)  # form re-rendered with errors
        self.assertContains(response, "errornote")
        self.assertEqual(Assignment.objects.count(), before)

    def test_create_repair_ticket_puts_the_asset_in_repair(self):
        asset = Asset.objects.get(serial_number="DU2723-9003")
        tech = User.objects.get(username="john.mathew")
        response = self.client.post(
            self.url("repairticket", "add"),
            {"asset": asset.pk, "issue": "No signal on HDMI", "status": "open", "assigned_technician": tech.pk},
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(status_of(asset), Asset.Status.IN_REPAIR)

    def test_resolving_a_ticket_in_the_admin_form_restores_the_asset(self):
        ticket = RepairTicket.objects.get(issue__startswith="Paper jam")
        response = self.client.post(
            self.url("repairticket", "change", ticket.pk),
            {"issue": ticket.issue, "status": "resolved", "assigned_technician": ""},
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(status_of(ticket.asset), Asset.Status.AVAILABLE)
        ticket.refresh_from_db()
        self.assertIsNotNone(ticket.resolved_at)

    def test_create_user_with_a_role_through_the_admin(self):
        response = self.client.post(
            self.url("user", "add"),
            {
                "username": "new.hire",
                "usable_password": "true",
                "password1": PASSWORD,
                "password2": PASSWORD,
                "first_name": "New",
                "last_name": "Hire",
                "email": "new.hire@example.com",
                "phone": "",
                "department": "Sales",
                "role": "admin",
            },
        )
        self.assertEqual(response.status_code, 302, getattr(response, "context", None) and response.context["adminform"].errors)
        user = User.objects.get(username="new.hire")
        self.assertEqual(user.role, Role.ADMIN)
        self.assertTrue(user.is_staff and user.is_superuser)
        self.assertTrue(user.check_password(PASSWORD))

    def test_inventory_quantities_can_be_edited_in_the_list(self):
        item = InventoryItem.objects.get(name="Sticky notes 3x3 in, 12-pack")
        others = list(InventoryItem.objects.order_by("name"))
        data = {
            "form-TOTAL_FORMS": len(others),
            "form-INITIAL_FORMS": len(others),
            "form-MIN_NUM_FORMS": 0,
            "form-MAX_NUM_FORMS": 1000,
            "_save": "Save",
        }
        for i, obj in enumerate(others):
            data[f"form-{i}-id"] = obj.pk
            data[f"form-{i}-quantity"] = 30 if obj.pk == item.pk else obj.quantity
            data[f"form-{i}-threshold"] = obj.threshold
        response = self.client.post(self.url("inventoryitem", "changelist"), data)
        self.assertEqual(response.status_code, 302)
        item.refresh_from_db()
        self.assertEqual(item.quantity, 30)
        self.assertFalse(item.is_low_stock)

    # -- bulk actions ----------------------------------------------------------

    def run_action(self, model, action, *objs):
        return self.client.post(
            self.url(model, "changelist"),
            {"action": action, "_selected_action": [o.pk for o in objs], "index": "0"},
            follow=True,
        )

    def test_retire_action(self):
        free = Asset.objects.get(serial_number="DL5440-A1003")
        held = Asset.objects.get(serial_number="DL5440-A1001")
        response = self.run_action("asset", "retire_assets", free, held)
        self.assertEqual(status_of(free), Asset.Status.RETIRED)
        self.assertEqual(status_of(held), Asset.Status.ASSIGNED)  # refused, still assigned
        self.assertContains(response, "Retired 1 asset(s)")
        self.assertContains(response, "still assigned to someone")

    def test_reinstate_action(self):
        retired = Asset.objects.get(serial_number="FUR-CH-0003")
        self.run_action("asset", "reinstate_assets", retired)
        self.assertEqual(status_of(retired), Asset.Status.AVAILABLE)

    def test_mark_returned_action(self):
        assignment = Assignment.objects.get(asset__serial_number="DL5440-A1001", date_returned__isnull=True)
        self.run_action("assignment", "mark_returned_today", assignment)
        assignment.refresh_from_db()
        self.assertEqual(assignment.date_returned, timezone.localdate())
        self.assertEqual(status_of(assignment.asset), Asset.Status.AVAILABLE)

    def test_ticket_actions(self):
        ticket = RepairTicket.objects.get(issue__startswith="Image is dim")
        self.run_action("repairticket", "mark_in_progress", ticket)
        ticket.refresh_from_db()
        self.assertEqual(ticket.status, RepairTicket.Status.IN_PROGRESS)
        self.run_action("repairticket", "mark_resolved", ticket)
        ticket.refresh_from_db()
        self.assertEqual(ticket.status, RepairTicket.Status.RESOLVED)
        self.assertIsNotNone(ticket.resolved_at)
        self.assertEqual(status_of(ticket.asset), Asset.Status.AVAILABLE)

    # -- access ----------------------------------------------------------------

    def test_employees_cannot_open_the_admin(self):
        self.client.force_login(User.objects.get(username="priya.nair"))
        response = self.client.get(reverse("admin:index"))
        self.assertEqual(response.status_code, 302)
        self.assertIn("/admin/login/", response["Location"])

    def test_anonymous_users_are_sent_to_the_admin_login(self):
        self.client.logout()
        response = self.client.get(self.url("asset", "changelist"))
        self.assertEqual(response.status_code, 302)


# --------------------------------------------------------------------------- #
# Minimal HTML pages
# --------------------------------------------------------------------------- #

class SiteViewTests(BaseTestCase):
    @classmethod
    def setUpTestData(cls):
        seed()

    def login(self, username, password="Saims@12345"):
        return self.client.post(reverse("login"), {"username": username, "password": password})

    def test_technician_is_shown_by_name_without_the_username(self):
        self.login("rahul.menon")
        response = self.client.get(reverse("home"))
        self.assertContains(response, "John Mathew")
        self.assertNotContains(response, "john.mathew")

    def test_home_requires_login(self):
        response = self.client.get(reverse("home"))
        self.assertRedirects(response, f"{reverse('login')}?next=/", fetch_redirect_response=False)

    def test_login_page_renders(self):
        response = self.client.get(reverse("login"))
        self.assertContains(response, "Log in")
        self.assertContains(response, "csrfmiddlewaretoken")

    def test_wrong_password_shows_an_error(self):
        response = self.login("priya.nair", "nope")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "correct username and password")

    def test_inactive_accounts_cannot_log_in(self):
        User.objects.filter(username="priya.nair").update(is_active=False)
        response = self.login("priya.nair")
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_employee_sees_only_their_own_assets(self):
        self.assertRedirects(self.login("priya.nair"), reverse("home"), fetch_redirect_response=False)
        response = self.client.get(reverse("home"))
        self.assertEqual(response.status_code, 200)
        serials = {a.asset.serial_number for a in response.context["my_assignments"]}
        self.assertEqual(serials, {"DL5440-A1001", "DU2723-9001", "FUR-CH-0001"})
        self.assertNotContains(response, "MBP14-C02XK1")  # David's laptop
        self.assertNotContains(response, "Admin panel")
        self.assertContains(response, "Employee")

    def test_employee_sees_open_repairs_on_their_assets(self):
        self.login("rahul.menon")
        response = self.client.get(reverse("home"))
        tickets = list(response.context["repairs_on_my_assets"])
        self.assertEqual(len(tickets), 1)
        self.assertIn("Battery drains", tickets[0].issue)
        self.assertContains(response, "John Mathew")

    def test_resolved_repairs_are_not_listed_as_open(self):
        self.login("priya.nair")  # her monitor had a ticket, now resolved
        response = self.client.get(reverse("home"))
        self.assertEqual(list(response.context["repairs_on_my_assets"]), [])
        self.assertContains(response, "None of your assets are being repaired.")

    def test_technician_sees_tickets_assigned_to_them(self):
        self.login("john.mathew")
        response = self.client.get(reverse("home"))
        self.assertEqual(len(response.context["tickets_assigned_to_me"]), 1)
        self.assertContains(response, "Repair tickets assigned to me")

    def test_empty_state_for_someone_with_no_assets(self):
        make_user("nobody", first_name="No")
        self.client.login(username="nobody", password=PASSWORD)
        response = self.client.get(reverse("home"))
        self.assertContains(response, "Nothing is assigned to you right now")
        self.assertNotContains(response, "Repair tickets assigned to me")

    def test_admin_sees_a_link_to_the_admin_panel(self):
        self.login("admin")
        response = self.client.get(reverse("home"))
        self.assertContains(response, reverse("admin:index"))

    def test_logout_requires_post_and_ends_the_session(self):
        self.login("priya.nair")
        self.assertEqual(self.client.get(reverse("logout")).status_code, 405)
        response = self.client.post(reverse("logout"))
        self.assertRedirects(response, reverse("login"), fetch_redirect_response=False)
        self.assertNotIn("_auth_user_id", self.client.session)


# --------------------------------------------------------------------------- #
# seed_data command
# --------------------------------------------------------------------------- #

class SeedCommandTests(BaseTestCase):
    def counts(self):
        return {
            "users": User.objects.count(),
            "assets": Asset.objects.count(),
            "assignments": Assignment.objects.count(),
            "tickets": RepairTicket.objects.count(),
            "inventory": InventoryItem.objects.count(),
        }

    def test_refuses_to_run_with_debug_off_unless_forced(self):
        self.assertFalse(settings.DEBUG)
        with self.assertRaises(CommandError):
            call_command("seed_data", stdout=io.StringIO())
        self.assertEqual(User.objects.count(), 0)

    def test_runs_without_force_when_debug_is_on(self):
        with self.settings(DEBUG=True):
            call_command("seed_data", stdout=io.StringIO())
        self.assertGreater(Asset.objects.count(), 0)

    def test_seeds_the_expected_data(self):
        seed()
        self.assertEqual(
            self.counts(),
            {"users": 11, "assets": 29, "assignments": 23, "tickets": 10, "inventory": 15},
        )
        by_status = {s: Asset.objects.filter(status=s).count() for s in Asset.Status.values}
        self.assertEqual(by_status, {"assigned": 14, "available": 9, "in_repair": 4, "retired": 2})
        self.assertEqual(User.objects.filter(role=Role.ADMIN).count(), 1)
        self.assertEqual(InventoryItem.objects.low_stock().count(), 5)

    def test_every_seeded_asset_status_matches_its_records(self):
        seed()
        for asset in Asset.objects.all():
            with self.subTest(asset=asset.serial_number):
                recorded = asset.status
                asset.sync_status()
                self.assertEqual(asset.status, recorded)

    def test_seeded_dates_are_sane(self):
        seed()
        today = timezone.localdate()
        for asset in Asset.objects.all():
            self.assertLessEqual(asset.purchase_date, today)
        for assignment in Assignment.objects.all():
            self.assertGreaterEqual(assignment.date_assigned, assignment.asset.purchase_date)
        for ticket in RepairTicket.objects.filter(resolved_at__isnull=False):
            self.assertGreaterEqual(ticket.resolved_at, ticket.created_at)

    def test_running_twice_adds_nothing(self):
        seed()
        first = self.counts()
        seed()
        self.assertEqual(self.counts(), first)

    def test_rerunning_does_not_clobber_edits(self):
        seed()
        InventoryItem.objects.filter(name="Wireless mouse").update(quantity=99)
        seed()
        self.assertEqual(InventoryItem.objects.get(name="Wireless mouse").quantity, 99)

    def test_reset_restores_the_original_data(self):
        seed()
        InventoryItem.objects.filter(name="Wireless mouse").update(quantity=99)
        make_asset(serial_number="EXTRA-1")
        call_command("seed_data", "--force", "--reset", stdout=io.StringIO())
        self.assertEqual(InventoryItem.objects.get(name="Wireless mouse").quantity, 14)
        self.assertFalse(Asset.objects.filter(serial_number="EXTRA-1").exists())
        self.assertEqual(self.counts()["assets"], 29)

    def test_reset_keeps_admin_accounts(self):
        seed()
        make_user("other.admin", role=Role.ADMIN)
        call_command("seed_data", "--force", "--reset", stdout=io.StringIO())
        self.assertTrue(User.objects.filter(username="other.admin").exists())
        self.assertTrue(User.objects.filter(username="admin").exists())

    def test_demo_accounts_can_log_in(self):
        seed()
        self.assertTrue(self.client.login(username="admin", password="Saims@12345"))
        self.client.logout()
        self.assertTrue(self.client.login(username="priya.nair", password="Saims@12345"))

    def test_custom_password_option(self):
        call_command("seed_data", "--force", "--password", "Another-Pass-9!", stdout=io.StringIO())
        self.assertTrue(self.client.login(username="priya.nair", password="Another-Pass-9!"))


# --------------------------------------------------------------------------- #
# Environment-variable helpers and settings
# --------------------------------------------------------------------------- #

class EnvLoaderTests(BaseTestCase):
    def write_env(self, text, encoding="utf-8"):
        handle = tempfile.NamedTemporaryFile("w", suffix=".env", delete=False, encoding=encoding)
        handle.write(text)
        handle.close()
        self.addCleanup(os.unlink, handle.name)
        return Path(handle.name)

    def test_parses_values_comments_and_quotes(self):
        path = self.write_env(
            "# a comment\n"
            "\n"
            "SAIMS_T_PLAIN=hello\n"
            'SAIMS_T_DOUBLE="with spaces"\n'
            "SAIMS_T_SINGLE='single'\n"
            "SAIMS_T_EQUALS=a=b=c\n"
            "  SAIMS_T_PADDED  =  padded  \n"
            "not a valid line\n"
        )
        keys = ["SAIMS_T_PLAIN", "SAIMS_T_DOUBLE", "SAIMS_T_SINGLE", "SAIMS_T_EQUALS", "SAIMS_T_PADDED"]
        with mock.patch.dict(os.environ):
            for key in keys:
                os.environ.pop(key, None)
            load_dotenv(path)
            self.assertEqual(os.environ["SAIMS_T_PLAIN"], "hello")
            self.assertEqual(os.environ["SAIMS_T_DOUBLE"], "with spaces")
            self.assertEqual(os.environ["SAIMS_T_SINGLE"], "single")
            self.assertEqual(os.environ["SAIMS_T_EQUALS"], "a=b=c")
            self.assertEqual(os.environ["SAIMS_T_PADDED"], "padded")

    def test_real_environment_variables_win_over_the_file(self):
        path = self.write_env("SAIMS_T_WINS=from-file\n")
        with mock.patch.dict(os.environ, {"SAIMS_T_WINS": "from-env"}):
            load_dotenv(path)
            self.assertEqual(os.environ["SAIMS_T_WINS"], "from-env")

    def test_tolerates_a_windows_bom(self):
        path = self.write_env("SAIMS_T_BOM=ok\n", encoding="utf-8-sig")
        with mock.patch.dict(os.environ):
            os.environ.pop("SAIMS_T_BOM", None)
            load_dotenv(path)
            self.assertEqual(os.environ["SAIMS_T_BOM"], "ok")

    def test_a_missing_file_is_ignored(self):
        load_dotenv(Path("/definitely/not/here/.env"))

    def test_bool_and_list_helpers(self):
        with mock.patch.dict(os.environ, {"T_YES": "Yes", "T_ONE": "1", "T_NO": "false", "T_HOSTS": " a.com, b.com ,,"}):
            self.assertTrue(env_bool("T_YES"))
            self.assertTrue(env_bool("T_ONE"))
            self.assertFalse(env_bool("T_NO", True))
            self.assertTrue(env_bool("T_MISSING", True))
            self.assertEqual(env_list("T_HOSTS"), ["a.com", "b.com"])
            self.assertEqual(env_list("T_MISSING"), [])

    def test_settings_refuse_to_start_without_a_secret_key(self):
        # An *empty* variable counts as set for the loader, so a developer's own .env can't mask this.
        env = {**os.environ, "DJANGO_SECRET_KEY": ""}
        result = subprocess.run(
            [sys.executable, "manage.py", "check"],
            cwd=settings.BASE_DIR,
            env=env,
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("DJANGO_SECRET_KEY is not set", result.stderr)


class ProductionSwitchTests(BaseTestCase):
    """DJANGO_HTTPS / DJANGO_USE_WHITENOISE are read at start-up, so each case runs in a fresh process."""

    PROBE = (
        "from django.conf import settings as s; "
        "print(s.SESSION_COOKIE_SECURE, s.CSRF_COOKIE_SECURE, s.SECURE_PROXY_SSL_HEADER, "
        "any('whitenoise' in m for m in s.MIDDLEWARE))"
    )

    def probe(self, **flags):
        env = {**os.environ, "DJANGO_SETTINGS_MODULE": "SAIMSadmin.settings", "DJANGO_SECRET_KEY": "x", **flags}
        result = subprocess.run(
            [sys.executable, "-c", self.PROBE], cwd=settings.BASE_DIR, env=env, capture_output=True, text=True
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.strip()

    def test_both_switches_are_off_by_default(self):
        self.assertEqual(
            self.probe(DJANGO_HTTPS="", DJANGO_USE_WHITENOISE=""), "False False None False"
        )

    def test_https_switch_secures_cookies_and_trusts_the_proxy_header(self):
        self.assertEqual(
            self.probe(DJANGO_HTTPS="True", DJANGO_USE_WHITENOISE=""),
            "True True ('HTTP_X_FORWARDED_PROTO', 'https') False",
        )
