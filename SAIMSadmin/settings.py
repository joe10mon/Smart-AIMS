"""
Django settings for the Smart Asset & Inventory Management System (SAIMS).

Everything environment-specific is read from environment variables (or from a
``.env`` file in the project root). See ``.env.example`` for the full list.

Docs: https://docs.djangoproject.com/en/5.2/ref/settings/
"""

from pathlib import Path

from django.core.exceptions import ImproperlyConfigured

from .env import env, env_bool, env_list, load_dotenv

# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent

# Read .env first so every setting below can see it. Real environment
# variables take precedence over values in the file.
load_dotenv(BASE_DIR / ".env")


# --- Core ---------------------------------------------------------------------

# Safe by default: DEBUG is off unless explicitly enabled.
DEBUG = env_bool("DJANGO_DEBUG", False)

SECRET_KEY = env("DJANGO_SECRET_KEY")
if not SECRET_KEY:
    raise ImproperlyConfigured(
        "DJANGO_SECRET_KEY is not set. Copy .env.example to .env "
        "(or set the environment variable) and try again."
    )

ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1")

# Needed once the app is served over HTTPS behind a proxy (Docker/HTTPS module),
# e.g. DJANGO_CSRF_TRUSTED_ORIGINS=https://assets.example.com
CSRF_TRUSTED_ORIGINS = env_list("DJANGO_CSRF_TRUSTED_ORIGINS")


# --- Applications -------------------------------------------------------------

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "SAIMS.apps.SaimsConfig",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "SAIMSadmin.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "SAIMSadmin.wsgi.application"


# --- Database -----------------------------------------------------------------
# SQLite by default. Set DB_ENGINE=postgres (plus DB_* variables) for Docker /
# production. The Postgres branch also needs the `psycopg` package installed.

DB_ENGINE = (env("DB_ENGINE", "sqlite") or "sqlite").lower()

if DB_ENGINE == "sqlite":
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": Path(env("SQLITE_PATH", str(BASE_DIR / "db.sqlite3"))),
        }
    }
elif DB_ENGINE == "postgres":
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": env("DB_NAME", "saims"),
            "USER": env("DB_USER", "saims"),
            "PASSWORD": env("DB_PASSWORD", ""),
            "HOST": env("DB_HOST", "localhost"),
            "PORT": env("DB_PORT", "5432"),
        }
    }
else:
    raise ImproperlyConfigured(f"Unsupported DB_ENGINE {DB_ENGINE!r}. Use 'sqlite' or 'postgres'.")


# --- Authentication -----------------------------------------------------------

# Custom user model (roles: Admin / Employee). This must be set before the
# first migration is created, which is why it lives here from day one.
AUTH_USER_MODEL = "SAIMS.User"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "home"
LOGOUT_REDIRECT_URL = "login"


# --- Internationalisation -----------------------------------------------------

LANGUAGE_CODE = "en-us"

# "Today" for assignment dates follows this zone, e.g. DJANGO_TIME_ZONE=Asia/Kolkata
TIME_ZONE = env("DJANGO_TIME_ZONE", "UTC")

USE_I18N = True

USE_TZ = True


# --- Static files -------------------------------------------------------------

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"  # target of `collectstatic` (used when deploying)


# --- Production switches ------------------------------------------------------
# Both are off by default, so local development behaves exactly as before.
# See DEPLOYMENT.md for when to turn them on.

# Serve /static/ from the app itself. Needed in Docker, where nothing else does;
# NOT needed on PythonAnywhere, which serves /static/ from its Web tab.
# Requires the `whitenoise` package (listed in requirements-prod.txt).
if env_bool("DJANGO_USE_WHITENOISE", False):
    MIDDLEWARE.insert(1, "whitenoise.middleware.WhiteNoiseMiddleware")
    STORAGES = {
        "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
        "staticfiles": {"BACKEND": "whitenoise.storage.CompressedStaticFilesStorage"},
    }

# Set once the site is reached over HTTPS: marks the session and CSRF cookies
# as secure, and trusts the proxy's X-Forwarded-Proto header to detect HTTPS.
if env_bool("DJANGO_HTTPS", False):
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True


# --- Misc ---------------------------------------------------------------------

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
