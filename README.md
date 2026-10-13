# Smart Asset & Inventory Management System (SAIMS)

**Module 1: Django backend + admin panel.** Tracks company assets, consumable
inventory, who holds what, and repair tickets. Built with Django 5.2.

| Module 1 requirement | Where |
| --- | --- |
| Project & app structure, environment variables | `SAIMSadmin/` (project), `SAIMS/` (app), `SAIMSadmin/settings.py`, `.env.example` |
| Custom user model with roles (Admin, Employee) | `SAIMS/models.py` → `User`, `Role` |
| Asset, InventoryItem, Assignment, RepairTicket | `SAIMS/models.py` |
| Admin with list filters, search, ordering | `SAIMS/admin.py` |
| Minimal HTML for testing | `/` (my assets), `/accounts/login/`, `SAIMS/templates/` |
| Dummy data | `python manage.py seed_data` |

## Quick start

Run from the folder that contains `manage.py`.

**Windows (PowerShell)**

```powershell
cd SAIMS\Smart-AIMS
..\demoenv\Scripts\Activate.ps1        # your virtualenv
pip install -r requirements.txt
copy .env.example .env                 # then edit if you like
python manage.py migrate
python manage.py seed_data             # dummy data
python manage.py runserver
```

macOS / Linux: same, but `source ../demoenv/bin/activate` and `cp .env.example .env`.

Then open:

* http://127.0.0.1:8000/admin/ — the admin panel (Admin accounts)
* http://127.0.0.1:8000/ — the minimal employee page (any account)

> **Already ran `migrate` before this version?** Delete your old `db.sqlite3`
> first. The project now uses a custom user model, and Django can't switch an
> existing database over to one.

### Demo accounts

`seed_data` creates one admin and ten employees. All share the password
**`Saims@12345`** (change it with `--password`).

| Username | Role | Notes |
| --- | --- | --- |
| `admin` | Admin | Full access to the admin panel |
| `priya.nair`, `rahul.menon`, `david.chen`, … | Employee | Can log in to `/`; can't open `/admin/` |
| `john.mathew`, `arun.kumar` | Employee | IT Support; the technicians on the repair tickets |

`seed_data` is safe to run again (it adds nothing the second time) and refuses
to run when `DJANGO_DEBUG` is off unless you pass `--force`.
`seed_data --reset` wipes assets, assignments, tickets, inventory and the demo
employees, then re-creates them. Admin accounts are never deleted.

## Data model

* **User**: Django's user plus `role` (`admin` / `employee`), `department`, `phone`.
* **Asset**: `name`, `type`, `serial_number` (unique), `status`, `purchase_date`.
* **Assignment**: `asset` → `employee`, `date_assigned`, `date_returned` (empty = still held).
* **RepairTicket**: `asset`, `issue`, `status`, `assigned_technician`, plus `resolved_at` (set automatically).
* **InventoryItem**: `name`, `item_type`, `quantity`, `threshold`. Low stock means `quantity <= threshold`.

All models also carry `created_at` / `updated_at`.

## Business rules

These live in the models, so they apply in the admin, the shell, the seed
command and any future API alike.

* **Roles.** `role` decides access. Admin switches on `is_staff` and
  `is_superuser`; Employee switches both off. They're recomputed on every save,
  so they can't be set independently (the admin form doesn't show them).
  `createsuperuser` creates an Admin-role account.
* **Asset status is derived, not typed in.** Priority: *Retired* (manual) >
  *In repair* (has an open ticket) > *Assigned* (has an active assignment) >
  *Available*. It updates itself whenever an assignment or ticket changes, so
  it can't drift out of sync. Open = Open or In progress. An asset that is
  assigned and then breaks goes to *In repair*, and returns to *Assigned* when
  the ticket is resolved.
* **One holder at a time.** An asset can have only one active assignment
  (database constraint). A return date can't be before the assigned date
  (database constraint).
* **Retired / in-repair assets** can't be assigned; retired assets can't get
  open tickets. An asset can be retired only when nobody holds it and it has no
  open tickets.
* **History is protected.** Assets and users with assignment/ticket history
  can't be deleted (retire the asset or deactivate the user instead).
* Purchase dates can't be in the future. Assets can't go to inactive users.

## Admin panel

* **Filters, search and ordering** on every model. For example: assets by status
  and type; assignments by *currently assigned / returned*, asset type and
  department; tickets by status, technician and asset type; inventory by item
  type and *stock level* (low / out / OK). Date drill-down on asset purchase
  date, assignment date and ticket creation date.
* **Asset page** shows read-only assignment and repair history. Status is
  display-only there; use the **Retire** / **Reinstate** actions on the list.
* **Assignments**: only available assets can be picked. Once saved, the asset
  and employee are locked; record a return instead. Action: *Mark returned (today)*.
* **Tickets**: actions *Mark in progress* / *Mark resolved*.
* **Inventory**: edit quantity and threshold straight from the list.
* **Users**: pick a role when creating a user.

## Configuration

Settings come from environment variables or a `.env` file (real environment
variables win). The `.env` loader is built in, so there's nothing extra to
install. See `.env.example`.

| Variable | Default | Purpose |
| --- | --- | --- |
| `DJANGO_SECRET_KEY` | *required* | Startup fails with a clear message if missing |
| `DJANGO_DEBUG` | `False` | Set `True` for development |
| `DJANGO_ALLOWED_HOSTS` | `localhost,127.0.0.1` | Comma-separated |
| `DJANGO_CSRF_TRUSTED_ORIGINS` | *(empty)* | Needed behind HTTPS / a proxy |
| `DJANGO_TIME_ZONE` | `UTC` | e.g. `Asia/Kolkata`; decides what "today" means |
| `DJANGO_HTTPS` | `False` | `True` once served over HTTPS: secure cookies, trust `X-Forwarded-Proto` |
| `DJANGO_USE_WHITENOISE` | `False` | Serve static files from the app (Docker sets this) |
| `DB_ENGINE` | `sqlite` | `sqlite` or `postgres` |
| `SQLITE_PATH`, `DB_NAME`, `DB_USER`, `DB_PASSWORD`, `DB_HOST`, `DB_PORT` | see file | Database settings |

The `postgres` option is wired up for the Docker module but has **not** been
exercised against a live Postgres server yet, and needs the `psycopg` package.

## Tests

```
python manage.py test
```

About 100 tests, a few seconds. They cover the roles, every business rule
(including the database constraints with model validation bypassed), the admin
(pages, search, filters, ordering, forms, actions, access control), the HTML
pages, the seed command and the `.env` loader.

## Deployment

See **[DEPLOYMENT.md](DEPLOYMENT.md)** for pushing to GitHub, hosting on
PythonAnywhere, and running with Docker. Note that PythonAnywhere does not run
Docker containers, so those are two separate routes.

## Project layout

```
Smart-AIMS/
├── manage.py
├── requirements.txt             Django (local + PythonAnywhere)
├── requirements-prod.txt        + gunicorn, whitenoise (Docker image)
├── .env.example
├── Dockerfile, docker-compose.yml, .dockerignore, docker/entrypoint.sh
├── DEPLOYMENT.md
├── SAIMSadmin/                  project: settings.py, env.py (.env loader), urls.py
└── SAIMS/                       app
    ├── models.py                models + business rules
    ├── signals.py               keeps Asset.status in sync
    ├── admin.py                 admin configuration
    ├── views.py, urls.py        minimal login / "my assets" pages
    ├── templates/               base.html, home.html, registration/login.html
    ├── management/commands/seed_data.py
    ├── migrations/0001_initial.py
    └── tests.py
```

## Not in this module

Dashboard and charts, a REST API and the full employee-facing UI (e.g. raising
tickets from the site) belong to later modules. The Docker setup is included
but hasn't been built or run yet (Docker wasn't available when it was written),
so expect to fix small things on first use; see DEPLOYMENT.md.
