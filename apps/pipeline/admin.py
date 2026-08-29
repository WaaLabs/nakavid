from django.contrib import admin
from django.db.models import BooleanField, ExpressionWrapper, Q, QuerySet, Subquery
from django.http import HttpRequest

from apps.pipeline.models import ScoringParams


@admin.register(ScoringParams)
class ScoringParamsAdmin(admin.ModelAdmin):
    """Expose the scoring knobs so tuning never needs a shell.

    Whether the resulting clips are genuinely good highlights is the founder's
    call on real footage (see AGENTS.md, "The scoring boundary") — this admin
    only makes the parameters reachable and the active set obvious.
    """

    list_display = (
        "pk",
        "is_active",
        "face_weight",
        "smile_weight",
        "motion_weight",
        "audio_weight",
        "window_size_seconds",
        "target_clip_length_seconds",
        "peak_count",
        "updated_at",
    )
    readonly_fields = ("created_at", "updated_at")
    ordering = ("-pk",)
    fieldsets = (
        (
            "Signal weights",
            {
                "description": (
                    "Relative contribution of each signal to a window's score. "
                    "They are normalised by their sum, so only the ratios matter."
                ),
                "fields": (
                    "face_weight",
                    "smile_weight",
                    "motion_weight",
                    "audio_weight",
                    "silence_penalty_weight",
                    "silence_rms_threshold",
                ),
            },
        ),
        (
            "Window slicing",
            {
                "description": (
                    "How the source is sampled before peaks are picked. "
                    "Detect max width downscales frames before face and smile "
                    "detection — 0 keeps full resolution. Detection is roughly "
                    "half of scoring time and scales with pixel count, so lowering "
                    "this is the main speed lever, at the cost of changing counts. "
                    "The Haar thresholds below decide how readily a face or a "
                    "smile is accepted: lower scale factor and fewer required "
                    "neighbours mean more detections and more false positives."
                ),
                "fields": (
                    "window_size_seconds",
                    "step_seconds",
                    "smoothing_window_count",
                    "frames_per_window",
                    "detect_max_width_pixels",
                    "face_scale_factor",
                    "face_min_neighbors",
                    "smile_scale_factor",
                    "smile_min_neighbors",
                    "smile_roi_min_height_pixels",
                ),
            },
        ),
        (
            "Clip selection",
            {
                "description": (
                    "How smoothed peaks become non-overlapping clips. "
                    "Target length sets how long each clip runs; minimum gap sets "
                    "how much silence must separate two of them, which is what stops "
                    "a single busy stretch being chopped into several near-adjacent clips."
                ),
                "fields": (
                    "target_clip_length_seconds",
                    "min_clip_length_seconds",
                    "min_gap_seconds",
                    "peak_count",
                ),
            },
        ),
        ("Timestamps", {"fields": ("created_at", "updated_at")}),
    )

    def get_queryset(self, request: HttpRequest) -> QuerySet[ScoringParams]:
        # get_active_scoring_params() takes the highest pk, so flag that row. One
        # subquery rather than a per-row lookup in is_active().
        return (
            super()
            .get_queryset(request)
            .annotate(
                active_flag=ExpressionWrapper(
                    Q(pk=Subquery(ScoringParams.objects.order_by("-pk").values("pk")[:1])),
                    output_field=BooleanField(),
                )
            )
        )

    def get_changeform_initial_data(self, request: HttpRequest) -> dict[str, object]:
        """Start a new set from the active one, so tuning is clone-and-tweak.

        Adding a row is the tuning workflow — the new pk becomes active — while
        editing in place would rewrite what already-run jobs record as their
        parameters. Prefilling makes the additive path the easy one.
        """
        active = ScoringParams.objects.order_by("-pk").first()
        if active is None:
            return super().get_changeform_initial_data(request)
        return {
            field.name: getattr(active, field.name)
            for field in ScoringParams._meta.fields
            if field.name not in {"id", "created_at", "updated_at"}
        }

    @admin.display(boolean=True, description="Active", ordering="active_flag")
    def is_active(self, obj: ScoringParams) -> bool:
        """The highest-pk row is what new score jobs use."""
        return bool(obj.active_flag)
