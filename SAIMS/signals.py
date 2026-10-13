"""Keep ``Asset.status`` in step with its assignments and repair tickets.

Signals (rather than ``save()`` overrides) are used because they also fire for
admin bulk deletes, which call ``QuerySet.delete()`` and bypass ``Model.delete()``.
"""

from django.db.models.signals import post_delete, post_save, pre_save
from django.dispatch import receiver

from .models import Asset, Assignment, RepairTicket


def _sync_asset(asset_id):
    asset = Asset.objects.filter(pk=asset_id).first()
    if asset is not None:
        asset.sync_status()


@receiver(pre_save, sender=Assignment)
@receiver(pre_save, sender=RepairTicket)
def remember_previous_asset(sender, instance, **kwargs):
    """If a record is moved to a different asset, the old asset needs re-syncing too."""
    previous = None
    if instance.pk:
        previous = sender.objects.filter(pk=instance.pk).values_list("asset_id", flat=True).first()
    instance._previous_asset_id = previous


@receiver(post_save, sender=Assignment)
@receiver(post_save, sender=RepairTicket)
def sync_after_save(sender, instance, **kwargs):
    _sync_asset(instance.asset_id)
    previous = getattr(instance, "_previous_asset_id", None)
    if previous and previous != instance.asset_id:
        _sync_asset(previous)


@receiver(post_delete, sender=Assignment)
@receiver(post_delete, sender=RepairTicket)
def sync_after_delete(sender, instance, **kwargs):
    _sync_asset(instance.asset_id)
