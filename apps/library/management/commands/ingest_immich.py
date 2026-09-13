"""Pull tagged videos from Immich into the library.

Footage already arrives in Immich by phone backup, so this is usually the
shortest path from filming something to having it scored — no second upload.
Tag whatever is ready to import with Nakavid/add in Immich, then run this;
only videos carrying that tag are pulled. Once a video is safely imported,
its tag moves from Nakavid/add to Nakavid/imported, so it doesn't sit there
looking ready to pull all over again on the next run.

The same pull also runs as a background job when triggered from the web
UI's "Scan Immich" button — see apps.library.immich_ingest.run_immich_ingest,
which this command is a thin CLI wrapper around.
"""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from apps.library.immich import ImmichError
from apps.library.immich_ingest import (
    DEFAULT_IMPORTED_TAG,
    DEFAULT_TAG,
    resolve_default_user,
    run_immich_ingest,
)
from apps.library.models import Video


class Command(BaseCommand):
    help = "Ingest Immich videos tagged for import."

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--tag",
            default=DEFAULT_TAG,
            help=f"Immich tag (full path) to pull videos from. Default: {DEFAULT_TAG!r}.",
        )
        parser.add_argument(
            "--imported-tag",
            default=DEFAULT_IMPORTED_TAG,
            help=(
                "Immich tag (full path) to move a video to once it's imported. "
                f"Default: {DEFAULT_IMPORTED_TAG!r}."
            ),
        )
        parser.add_argument("--class-name", default="", help="Optional. Free-text metadata.")
        parser.add_argument("--theme", default="", help="Optional. Free-text metadata.")
        parser.add_argument("--type", choices=["long", "short"], default="long")
        parser.add_argument("--user", help="Username to attribute the ingest to.")
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="List what would be pulled without downloading or writing rows.",
        )
        parser.add_argument("--limit", type=int, default=0, help="Stop after this many new assets.")

    def _resolve_user(self, username: str | None):
        if username:
            model = get_user_model()
            try:
                return model.objects.get(username=username)
            except model.DoesNotExist as exc:
                raise CommandError(f"No user named {username!r}") from exc
        try:
            return resolve_default_user()
        except ImmichError as exc:
            raise CommandError(str(exc)) from exc

    def handle(self, *args, **options) -> None:
        user = self._resolve_user(options["user"])
        video_type = Video.VideoType.TYPE_A if options["type"] == "long" else Video.VideoType.TYPE_B
        orientation = (
            Video.Orientation.LANDSCAPE
            if video_type == Video.VideoType.TYPE_A
            else Video.Orientation.MIXED
        )

        try:
            run_immich_ingest(
                user=user,
                tag=options["tag"],
                imported_tag=options["imported_tag"],
                video_type=video_type,
                orientation=orientation,
                class_name=options["class_name"],
                theme=options["theme"],
                dry_run=options["dry_run"],
                limit=options["limit"],
                write_out=self.stdout.write,
                write_err=self.stderr.write,
            )
        except ImmichError as exc:
            raise CommandError(str(exc)) from exc
