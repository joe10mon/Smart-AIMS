# Deployment guide

Replace `<username>` with your PythonAnywhere username and `<repo>` with your
GitHub repository name.

## Pick a path first

**Docker and PythonAnywhere are alternatives, not steps in one chain.**
PythonAnywhere runs your code directly and does not let you run your own Docker
containers. So:

| Path | Use it for | HTTPS |
| --- | --- | --- |
| **GitHub → PythonAnywhere** | Getting the app live quickly, no Docker | Automatic on `<username>.pythonanywhere.com` |
| **GitHub → Docker → a container host** | The "Docker + HTTPS" deliverable | You add a reverse proxy (e.g. Caddy) on a server with a domain |

Git comes first in both. The Docker files in this repo are harmless on
PythonAnywhere, which simply ignores them.

---

## 1. Push to GitHub

1. Install Git for Windows (https://git-scm.com), then in a new terminal:

   ```powershell
   git --version
   git config --global user.name "Your Name"
   git config --global user.email "you@example.com"
   ```

2. On github.com, click **New repository**. Leave **README, .gitignore and
   license unticked** (the project already has them; ticking them causes a
   conflict on your first push). A public repo is simplest for PythonAnywhere.

3. In the folder that contains `manage.py` (**not** the parent folder that
   holds `demoenv`, or the virtualenv gets committed too):

   ```powershell
   cd SAIMS\Smart-AIMS
   git init -b main
   git add .
   git status
   ```

   Read the `git status` list. It must **not** contain `.env`, `db.sqlite3`,
   `demoenv/` or `__pycache__/` (`.gitignore` excludes them). If you see any of
   them, stop and fix that first.

   ```powershell
   git commit -m "Module 1: Django backend, models, admin, seed data"
   git remote add origin https://github.com/<you>/<repo>.git
   git push -u origin main
   ```

   A browser window opens to sign in to GitHub. Account passwords aren't
   accepted on the command line any more, so if you're asked for one, use a
   personal access token instead.

4. Refresh the repo page and confirm `.env` is not there. If a secret ever gets
   committed, generate a new one, because deleting the file doesn't remove it
   from history.

---

## 2. Host on PythonAnywhere (no Docker)

**Free plan, as of October 2026:** one web app at
`<username>.pythonanywhere.com`, no custom domain, 512 MiB disk, outbound
internet limited to an allowlist (GitHub is on it), and the web app expires
after one month unless you extend it from the **Web** tab. New free accounts
have no MySQL, which is fine because this project uses SQLite.

1. Sign up at https://www.pythonanywhere.com (Beginner account).

2. **Consoles → Bash**, then:

   ```bash
   git clone https://github.com/<you>/<repo>.git
   cd <repo>
   ls /usr/bin/python3.1*          # see which versions exist; Django 5.2 needs 3.10 or newer
   mkvirtualenv saims --python=/usr/bin/python3.12   # use a version from the list
   pip install -r requirements.txt
   ```

   Use `requirements.txt` here, not `requirements-prod.txt` (that one is for Docker).

3. Create the settings file and a fresh secret key:

   ```bash
   cp .env.example .env
   python -c "import secrets; print(secrets.token_urlsafe(50))"
   nano .env
   ```

   Set these lines (paste the generated key; save with Ctrl+O, exit with Ctrl+X):

   ```
   DJANGO_SECRET_KEY=<the generated key>
   DJANGO_DEBUG=False
   DJANGO_ALLOWED_HOSTS=<username>.pythonanywhere.com
   DJANGO_CSRF_TRUSTED_ORIGINS=https://<username>.pythonanywhere.com
   DJANGO_HTTPS=True
   ```

4. Set up the database, static files and your own admin account:

   ```bash
   python manage.py migrate
   python manage.py collectstatic --noinput
   python manage.py createsuperuser
   ```

   Optional demo data: `python manage.py seed_data --force --password "<a strong password>"`.
   The site is public, so don't leave the default demo password (`Saims@12345`,
   which is in the repo) on accounts anyone can reach.

5. **Web** tab → **Add a new web app** → Next → **Manual configuration** (not
   the Django wizard) → choose the same Python version as your virtualenv.

6. On the Web tab, fill in:
   * **Source code:** `/home/<username>/<repo>`
   * **Virtualenv:** `/home/<username>/.virtualenvs/saims`
   * **Static files:** URL `/static/` → Directory `/home/<username>/<repo>/staticfiles`
   * **Force HTTPS:** on
   * Click the **WSGI configuration file** link and replace everything in it with:

     ```python
     import os
     import sys

     path = "/home/<username>/<repo>"
     if path not in sys.path:
         sys.path.insert(0, path)

     os.environ["DJANGO_SETTINGS_MODULE"] = "SAIMSadmin.settings"

     from django.core.wsgi import get_wsgi_application
     application = get_wsgi_application()
     ```

   The `.env` file is found automatically, so there's nothing else to configure.

7. Click the green **Reload** button, then open
   `https://<username>.pythonanywhere.com/` and `/admin/`.

**Updating after you push changes:**

```bash
cd ~/<repo>
git pull
workon saims
pip install -r requirements.txt
python manage.py migrate
python manage.py collectstatic --noinput
```

then press **Reload** on the Web tab.

---

## 3. Docker

Requires Docker Desktop (or Docker Engine). Run these in the folder with `manage.py`.

```powershell
copy .env.example .env
# edit .env: set DJANGO_SECRET_KEY (see .env.example for a command that makes one)
docker compose up --build
```

Open http://localhost:8000. In a second terminal:

```powershell
docker compose exec web python manage.py createsuperuser
docker compose exec web python manage.py seed_data     # optional demo data (needs DJANGO_DEBUG=True)
```

| Command | Effect |
| --- | --- |
| `docker compose down` | Stop; the database is kept in the `saims_data` volume |
| `docker compose down -v` | Stop **and delete the database** |
| `docker compose logs -f web` | Watch the logs |
| `docker compose up --build -d` | Rebuild after code changes, in the background |

How it fits together: the image (`Dockerfile`) installs
`requirements-prod.txt` (Django + gunicorn + whitenoise), collects static files
at build time, and runs as a non-root user. On every start `docker/entrypoint.sh`
applies migrations and then launches gunicorn. whitenoise serves `/static/`
(switched on by `docker-compose.yml`). The SQLite database lives in a named
volume at `/data`.

**Going to a real server with HTTPS** (the remaining part of the brief): put the
container behind a reverse proxy that handles certificates, such as Caddy or
Nginx, on a machine with a domain name. Then set `DJANGO_DEBUG=False`,
`DJANGO_ALLOWED_HOSTS=<your domain>`, `DJANGO_CSRF_TRUSTED_ORIGINS=https://<your domain>`
and `DJANGO_HTTPS=True`. The proxy must send the `X-Forwarded-Proto` header
(Caddy does by default). For more than light use, switch `DB_ENGINE` to
`postgres` (add `psycopg` to `requirements-prod.txt` and a Postgres service).

---

## Troubleshooting

| Symptom | Likely cause and fix |
| --- | --- |
| `ImproperlyConfigured: DJANGO_SECRET_KEY is not set` | There's no `.env` in the folder with `manage.py`, or it lacks the key. |
| 400 Bad Request / `DisallowedHost` | The hostname isn't in `DJANGO_ALLOWED_HOSTS`. |
| 403 "CSRF verification failed" when logging in | Set `DJANGO_CSRF_TRUSTED_ORIGINS` to the full origin including `https://`. |
| Admin looks unstyled (plain HTML) | PythonAnywhere: you skipped `collectstatic` or the Static files mapping. Docker: `DJANGO_USE_WHITENOISE` isn't `True`. |
| `ModuleNotFoundError: No module named 'django'` on PythonAnywhere | The **Virtualenv** path on the Web tab is empty or wrong. |
| Changes don't show up on PythonAnywhere | Press **Reload** on the Web tab. |
| Any other server error on PythonAnywhere | Open the **Error log** link on the Web tab; the traceback is at the bottom. |
| `fatal: could not read Username` on `git clone` | The repo is private. Make it public, or clone with a personal access token. |
| Docker: `/bin/sh^M: bad interpreter` or `exec format error` | Windows line endings on `docker/entrypoint.sh`. The `.gitattributes` file prevents this; re-clone, or run `git add --renormalize .`. |
| Docker: `port is already allocated` | Something else is using 8000. Change the left side of `"8000:8000"` in `docker-compose.yml`. |
