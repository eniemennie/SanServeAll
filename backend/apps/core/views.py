"""
The public landing page (Row 1's entry point, matching the UI reference)
-- lives in core rather than accounts, since it's not itself an auth
screen, just the door to the two real ones (cashier login, admin login).
"""

from django.shortcuts import render


def landing(request):
    return render(request, "core/landing.html")
