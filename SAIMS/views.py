from django.contrib.auth.decorators import login_required
from django.shortcuts import render

from .models import Assignment, RepairTicket


@login_required
def home(request):
    """Minimal landing page: what the logged-in person holds and what's being repaired."""
    user = request.user
    open_statuses = RepairTicket.OPEN_STATUSES

    my_assignments = (
        Assignment.objects.filter(employee=user, date_returned__isnull=True)
        .select_related("asset")
        .order_by("asset__name")
    )
    # Both conditions sit in one filter() call so they match the same assignment row.
    repairs_on_my_assets = (
        RepairTicket.objects.filter(
            asset__assignments__employee=user,
            asset__assignments__date_returned__isnull=True,
            status__in=open_statuses,
        )
        .select_related("asset", "assigned_technician")
        .distinct()
    )
    tickets_assigned_to_me = RepairTicket.objects.filter(
        assigned_technician=user, status__in=open_statuses
    ).select_related("asset")

    return render(
        request,
        "SAIMS/home.html",
        {
            "my_assignments": my_assignments,
            "repairs_on_my_assets": repairs_on_my_assets,
            "tickets_assigned_to_me": tickets_assigned_to_me,
        },
    )
