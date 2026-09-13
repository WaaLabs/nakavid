from apps.library.immich import immich_is_configured


def immich(request):
    """Whether to show Immich-dependent UI (the nav's "Scan Immich" button).

    Global, not per-view, because the button lives in the shared nav.
    """
    return {"immich_configured": immich_is_configured()}
