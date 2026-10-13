from django.apps import AppConfig


class SaimsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "SAIMS"
    verbose_name = "Asset & inventory management"

    def ready(self):
        from . import signals  # noqa: F401  (registers the signal handlers)
