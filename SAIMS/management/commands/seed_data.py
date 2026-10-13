"""Insert dummy data for development and demos.

    python manage.py seed_data            # add anything that's missing
    python manage.py seed_data --reset    # wipe domain data + seeded employees first

Safe to run repeatedly: records are matched on serial number / username / name,
so a second run adds nothing. Everything is created through the normal model
code, so the business rules apply (asset statuses come out consistent) and the
whole run is one transaction.

Dates are relative to "today" so the data always looks recent.
"""

from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from SAIMS.models import Asset, Assignment, InventoryItem, RepairTicket, Role, User

DEFAULT_PASSWORD = "Saims@12345"
ADMIN_USERNAME = "admin"

A = Asset.AssetType
T = RepairTicket.Status
I = InventoryItem.ItemType  # noqa: E741  (short alias keeps the data table readable)

# username, first name, last name, department
EMPLOYEES = [
    ("priya.nair", "Priya", "Nair", "Engineering"),
    ("rahul.menon", "Rahul", "Menon", "Engineering"),
    ("david.chen", "David", "Chen", "Engineering"),
    ("anita.joseph", "Anita", "Joseph", "HR"),
    ("mohammed.faisal", "Mohammed", "Faisal", "Finance"),
    ("sara.thomas", "Sara", "Thomas", "Marketing"),
    ("maria.garcia", "Maria", "Garcia", "Sales"),
    ("emily.brown", "Emily", "Brown", "Operations"),
    ("john.mathew", "John", "Mathew", "IT Support"),  # technician
    ("arun.kumar", "Arun", "Kumar", "IT Support"),  # technician
]

# name, type, serial number, purchased N days ago
ASSETS = [
    ("Dell Latitude 5440", A.LAPTOP, "DL5440-A1001", 420),
    ("Dell Latitude 5440", A.LAPTOP, "DL5440-A1002", 420),
    ("Dell Latitude 5440", A.LAPTOP, "DL5440-A1003", 365),
    ('MacBook Pro 14" M3', A.LAPTOP, "MBP14-C02XK1", 300),
    ('MacBook Pro 14" M3', A.LAPTOP, "MBP14-C02XK2", 300),
    ("Lenovo ThinkPad T14", A.LAPTOP, "TP-T14-PF3A71", 700),
    ("Lenovo ThinkPad T14", A.LAPTOP, "TP-T14-PF3A72", 700),
    ("Dell Latitude 7400 (2019)", A.LAPTOP, "DL7400-0457", 2300),
    ("HP EliteDesk 800 G9", A.DESKTOP, "HP-ED800-7QR201", 500),
    ("HP EliteDesk 800 G9", A.DESKTOP, "HP-ED800-7QR202", 500),
    ("Dell UltraSharp U2723QE", A.MONITOR, "DU2723-9001", 410),
    ("Dell UltraSharp U2723QE", A.MONITOR, "DU2723-9002", 410),
    ("Dell UltraSharp U2723QE", A.MONITOR, "DU2723-9003", 410),
    ("LG 27UP850", A.MONITOR, "LG27-5521", 600),
    ("LG 27UP850", A.MONITOR, "LG27-5522", 600),
    ("iPhone 14", A.PHONE, "IP14-F2LXK001", 480),
    ("iPhone 14", A.PHONE, "IP14-F2LXK002", 480),
    ("iPad Air (5th gen)", A.TABLET, "IPA5-DMPX77", 350),
    ("HP LaserJet Pro M404dn", A.PRINTER, "HP-M404-PHBR12", 800),
    ("Canon imageRUNNER multifunction printer", A.PRINTER, "CN-IR2630-8841", 900),
    ("Ergonomic chair", A.FURNITURE, "FUR-CH-0001", 1000),
    ("Ergonomic chair", A.FURNITURE, "FUR-CH-0002", 1000),
    ("Ergonomic chair (old model)", A.FURNITURE, "FUR-CH-0003", 2800),
    ("Standing desk 160 cm", A.FURNITURE, "FUR-SD-0001", 650),
    ("Standing desk 160 cm", A.FURNITURE, "FUR-SD-0002", 650),
    ("Network cable tester", A.TOOL, "TL-FLK-3301", 900),
    ("Precision screwdriver kit", A.TOOL, "TL-PSK-0107", 400),
    ("Digital multimeter", A.TOOL, "TL-DMM-0045", 750),
    ("Epson EB-X49 projector", A.OTHER, "EP-X49-3KJ902", 1100),
]

# Past assignments that were already returned: serial, username, assigned N days ago, returned N days ago.
# Created first, while every asset is still available.
RETURNED_ASSIGNMENTS = [
    ("DL5440-A1003", "sara.thomas", 350, 120),
    ("MBP14-C02XK2", "maria.garcia", 290, 150),
    ("IP14-F2LXK002", "emily.brown", 470, 200),
    ("DL7400-0457", "john.mathew", 2200, 500),
    ("FUR-CH-0003", "anita.joseph", 2700, 900),
    ("LG27-5522", "rahul.menon", 580, 300),
    ("TP-T14-PF3A71", "arun.kumar", 690, 660),  # previous holder of a laptop that's now with someone else
    ("DL5440-A1001", "mohammed.faisal", 415, 402),
]

# Current assignments: serial, username, assigned N days ago.
ACTIVE_ASSIGNMENTS = [
    ("DL5440-A1001", "priya.nair", 400),
    ("DL5440-A1002", "rahul.menon", 400),
    ("MBP14-C02XK1", "david.chen", 280),
    ("TP-T14-PF3A71", "anita.joseph", 650),
    ("TP-T14-PF3A72", "mohammed.faisal", 650),
    ("DU2723-9001", "priya.nair", 395),
    ("DU2723-9002", "rahul.menon", 395),
    ("LG27-5521", "david.chen", 270),
    ("IP14-F2LXK001", "maria.garcia", 460),
    ("FUR-CH-0001", "priya.nair", 390),
    ("FUR-SD-0001", "david.chen", 260),
    ("IPA5-DMPX77", "sara.thomas", 300),
    ("HP-ED800-7QR201", "emily.brown", 480),
    ("TL-FLK-3301", "arun.kumar", 800),
    ("TL-PSK-0107", "john.mathew", 380),
]

# serial, issue, status, technician username (or None), created N days ago, resolved N days ago (or None)
REPAIR_TICKETS = [
    ("DL5440-A1002", "Battery drains in under 30 minutes and the case is bulging near the trackpad.", T.IN_PROGRESS, "john.mathew", 6, None),
    ("HP-M404-PHBR12", "Paper jam error persists even after the tray is cleared.", T.OPEN, None, 2, None),
    ("CN-IR2630-8841", "Prints come out faded and the display shows a toner sensor warning.", T.IN_PROGRESS, "arun.kumar", 4, None),
    ("EP-X49-3KJ902", "Image is dim and the lamp warning light is on.", T.OPEN, None, 1, None),
    ("DU2723-9001", "Screen flickers and goes black for a few seconds at random.", T.RESOLVED, "arun.kumar", 40, 36),
    ("TP-T14-PF3A72", "The E and R keys stopped responding.", T.RESOLVED, "john.mathew", 90, 85),
    ("HP-ED800-7QR202", "Won't boot; BIOS reports a disk error.", T.RESOLVED, "john.mathew", 120, 112),
    ("MBP14-C02XK2", "Trackpad click works only intermittently.", T.RESOLVED, "arun.kumar", 160, 152),
    ("DL7400-0457", "Motherboard failure; repair cost is higher than the laptop is worth.", T.RESOLVED, "john.mathew", 520, 505),
    ("IP14-F2LXK001", "Cracked screen after a drop.", T.CANCELLED, None, 30, None),
]

# Retired last: only possible once nobody holds the asset and its tickets are closed.
RETIRED_SERIALS = ["DL7400-0457", "FUR-CH-0003"]

# name, type, quantity, reorder threshold  (the first five rows of low-stock items are marked)
INVENTORY = [
    ("HDMI cable 2 m", I.CABLE, 25, 10),
    ("USB-C to USB-C cable 1 m", I.CABLE, 8, 10),  # low
    ("Cat6 patch cable 1 m", I.CABLE, 40, 15),
    ("DisplayPort cable 1.8 m", I.CABLE, 12, 6),
    ("LaserJet toner cartridge (black)", I.CONSUMABLE, 3, 5),  # low
    ("Canon toner cartridge (black)", I.CONSUMABLE, 6, 4),
    ("A4 copier paper, 500-sheet ream", I.CONSUMABLE, 120, 50),
    ("Sticky notes 3x3 in, 12-pack", I.CONSUMABLE, 0, 10),  # out of stock
    ("Wireless mouse", I.PERIPHERAL, 14, 8),
    ("Wireless keyboard", I.PERIPHERAL, 5, 8),  # low
    ("USB webcam 1080p", I.PERIPHERAL, 9, 4),
    ("Headset with microphone", I.PERIPHERAL, 11, 6),
    ("USB-C 65 W laptop charger", I.ACCESSORY, 2, 3),  # low
    ('Laptop sleeve 14"', I.ACCESSORY, 18, 5),
    ("HP LaserJet Pro M404dn (spare unit)", I.PRINTER, 2, 1),
]


class Command(BaseCommand):
    help = "Insert dummy users, assets, assignments, repair tickets and inventory (development/demo only)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--reset",
            action="store_true",
            help="Delete all assets, assignments, repair tickets, inventory and the seeded employee accounts first.",
        )
        parser.add_argument(
            "--password",
            default=DEFAULT_PASSWORD,
            help=f"Password for newly created demo accounts (default: {DEFAULT_PASSWORD}).",
        )
        parser.add_argument(
            "--force",
            action="store_true",
            help="Allow running while DEBUG is off (the command refuses by default).",
        )

    def handle(self, *args, **options):
        if not settings.DEBUG and not options["force"]:
            raise CommandError(
                "Refusing to insert dummy data while DEBUG is off. "
                "Set DJANGO_DEBUG=True for development, or pass --force if you really mean it."
            )

        self.today = timezone.localdate()
        self.now = timezone.now()

        with transaction.atomic():
            if options["reset"]:
                self._reset()
            users = self._create_users(options["password"])
            assets = self._create_assets()
            self._create_assignments(assets, users)
            self._create_tickets(assets, users)
            self._retire_assets()
            self._create_inventory()

        self._print_summary(options["password"])

    # -- steps -----------------------------------------------------------------

    def _days_ago(self, days):
        return self.today - timedelta(days=days)

    def _reset(self):
        RepairTicket.objects.all().delete()
        Assignment.objects.all().delete()
        Asset.objects.all().delete()
        InventoryItem.objects.all().delete()
        User.objects.filter(username__in=[e[0] for e in EMPLOYEES], role=Role.EMPLOYEE).delete()
        self.stdout.write("Existing assets, assignments, tickets, inventory and demo employees removed.")

    def _create_users(self, password):
        users = {}
        if not User.objects.filter(username=ADMIN_USERNAME).exists():
            User.objects.create_superuser(
                ADMIN_USERNAME,
                "admin@saims.local",
                password,
                first_name="System",
                last_name="Admin",
                department="IT Support",
            )
        users[ADMIN_USERNAME] = User.objects.get(username=ADMIN_USERNAME)

        for username, first_name, last_name, department in EMPLOYEES:
            user = User.objects.filter(username=username).first()
            if user is None:
                user = User.objects.create_user(
                    username=username,
                    email=f"{username}@saims.local",
                    password=password,
                    first_name=first_name,
                    last_name=last_name,
                    department=department,
                    role=Role.EMPLOYEE,
                )
            users[username] = user
        return users

    def _create_assets(self):
        assets = {}
        for name, asset_type, serial, purchased_days_ago in ASSETS:
            asset, _ = Asset.objects.get_or_create(
                serial_number=serial,
                defaults={"name": name, "type": asset_type, "purchase_date": self._days_ago(purchased_days_ago)},
            )
            assets[serial] = asset
        return assets

    def _create_assignments(self, assets, users):
        # History first (while assets are still available), then current assignments.
        for serial, username, assigned_ago, returned_ago in RETURNED_ASSIGNMENTS:
            self._assign(assets[serial], users[username], assigned_ago, returned_ago)
        for serial, username, assigned_ago in ACTIVE_ASSIGNMENTS:
            self._assign(assets[serial], users[username], assigned_ago)

    def _assign(self, asset, employee, assigned_ago, returned_ago=None):
        if Assignment.objects.filter(asset=asset, employee=employee).exists():
            return  # already seeded
        Assignment.objects.create(
            asset=asset,
            employee=employee,
            date_assigned=self._days_ago(assigned_ago),
            date_returned=None if returned_ago is None else self._days_ago(returned_ago),
        )

    def _create_tickets(self, assets, users):
        for serial, issue, status, technician, created_ago, resolved_ago in REPAIR_TICKETS:
            asset = assets[serial]
            if RepairTicket.objects.filter(asset=asset, issue=issue).exists():
                continue
            ticket = RepairTicket.objects.create(
                asset=asset,
                issue=issue,
                status=status,
                assigned_technician=users[technician] if technician else None,
            )
            # Back-date so charts/filters have a realistic spread (auto_now_add stamps "now").
            created_at = self.now - timedelta(days=created_ago)
            resolved_at = self.now - timedelta(days=resolved_ago) if resolved_ago is not None else None
            RepairTicket.objects.filter(pk=ticket.pk).update(
                created_at=created_at,
                resolved_at=resolved_at,
                updated_at=resolved_at or created_at,
            )

    def _retire_assets(self):
        for serial in RETIRED_SERIALS:
            Asset.objects.get(serial_number=serial).retire()

    def _create_inventory(self):
        for name, item_type, quantity, threshold in INVENTORY:
            InventoryItem.objects.get_or_create(
                name=name,
                defaults={"item_type": item_type, "quantity": quantity, "threshold": threshold},
            )

    # -- output ----------------------------------------------------------------

    def _print_summary(self, password):
        status = Asset.Status
        self.stdout.write(self.style.SUCCESS("Dummy data is in place."))
        self.stdout.write(
            f"  Users:            {User.objects.count()} "
            f"({User.objects.filter(role=Role.ADMIN).count()} admin, {User.objects.filter(role=Role.EMPLOYEE).count()} employees)"
        )
        self.stdout.write(
            f"  Assets:           {Asset.objects.count()} "
            f"({Asset.objects.filter(status=status.ASSIGNED).count()} assigned, "
            f"{Asset.objects.filter(status=status.AVAILABLE).count()} available, "
            f"{Asset.objects.filter(status=status.IN_REPAIR).count()} in repair, "
            f"{Asset.objects.filter(status=status.RETIRED).count()} retired)"
        )
        self.stdout.write(
            f"  Assignments:      {Assignment.objects.count()} "
            f"({Assignment.objects.filter(date_returned__isnull=True).count()} active)"
        )
        self.stdout.write(
            f"  Repair tickets:   {RepairTicket.objects.count()} "
            f"({RepairTicket.objects.filter(status__in=RepairTicket.OPEN_STATUSES).count()} open)"
        )
        self.stdout.write(
            f"  Inventory items:  {InventoryItem.objects.count()} "
            f"({InventoryItem.objects.low_stock().count()} low on stock)"
        )
        self.stdout.write("")
        self.stdout.write(f"Log in as '{ADMIN_USERNAME}' (Admin) or any employee, e.g. 'priya.nair'.")
        self.stdout.write(f"Password for newly created demo accounts: {password}")
