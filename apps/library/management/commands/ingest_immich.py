"""Pull videos from an Immich album, or tagged ones, into the library.

Footage already arrives in Immich by phone backup, so this is usually the
shortest path from filming something to having it scored — no second upload.

--album NAME (or its id) pulls every video in that album that isn't in the
library yet. It works for an album another account shared with this key's
account — which a tag can't, since Immich only lets an asset's owner tag it —
and writes nothing back to Immich: re-running it later pulls only what's
been added since.

Without --album, the pull is tag-based. Tag whatever is ready to import with
the configured "add" tag in Immich (Settings -> Ingest settings in the web
UI; Nakavid/add by default), then run this; only videos carrying that tag are
pulled. Once a video is safely imported, its tag moves to the configured
"imported" tag, so it doesn't sit there looking ready to pull all over again
on the next run.

Either way, long recordings and short ones are classified by their own probed
duration, not by which button or flag you used to pull them.

The same pull also runs as a background job when triggered from the web
UI's "From Immich" page — see apps.library.immich_ingest.run_immich_ingest,
which this command is a thin CLI wrapper around.
"""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from apps.library.immich import ImmichError
from apps.library.immich_ingest import resolve_default_user, run_immich_ingest
from apps.library.models import Video


class Command(BaseCommand):
    help = "Ingest new videos from an Immich album, or ones tagged for import."

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--album",
            default=None,
            help=(
                "Immich album name (or id) to pull new videos from, shared albums "
                "included. Overrides --tag; nothing is retagged."
            ),
        )
        parser.add_argument(
            "--tag",
            default=None,
            help=(
                "Immich tag (full path) to pull videos from. Default: the configured "
                "'add' tag (Settings -> Ingest settings in the web UI)."
            ),
        )
        parser.add_argument(
            "--imported-tag",
            default=None,
            help=(
                "Immich tag (full path) to move a video to once it's imported. "
                "Default: the configured 'imported' tag (Settings -> Ingest settings)."
            ),
        )
        parser.add_argument("--class-name", default="", help="Optional. Free-text metadata.")
        parser.add_argument("--theme", default="", help="Optional. Free-text metadata.")
        parser.add_argument(
            "--type",
            choices=["auto", "long", "short"],
            default="auto",
            help=(
                "Default: auto — classify each video long/short by its own probed "
                "duration. Pass long/short to force every video in this run to it."
            ),
        )
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
        video_type = {
            "auto": None,
            "long": Video.VideoType.TYPE_A,
            "short": Video.VideoType.TYPE_B,
        }[options["type"]]

        try:
            run_immich_ingest(
                user=user,
                album_id=options["album"],
                tag=options["tag"],
                imported_tag=options["imported_tag"],
                video_type=video_type,
                class_name=options["class_name"],
                theme=options["theme"],
                dry_run=options["dry_run"],
                limit=options["limit"],
                write_out=self.stdout.write,
                write_err=self.stderr.write,
            )
        except ImmichError as exc:
            raise CommandError(str(exc)) from exc
