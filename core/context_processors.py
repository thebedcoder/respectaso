from django.conf import settings


def version(request):
    """Expose VERSION and IS_NATIVE_APP to all templates."""
    return {
        "VERSION": settings.VERSION,
        "IS_NATIVE_APP": getattr(settings, "IS_NATIVE_APP", False),
    }
