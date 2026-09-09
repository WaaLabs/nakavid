"""Pull tagged videos from Immich into the library.

Footage already arrives in Immich by phone backup, so this is usually the
shortest path from filming something to having it scored — no second upload.
Tag whatever is ready to import with Nakavid/add in Immich, then run this;
only videos carrying that tag are pulled. Once a video is safely imported,
its tag moves from Nakavid/add to Nakavid/imported, so it doesn't sit there
looking ready to pull all over again on the next run.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone
from django.utils.text import get_valid_filename

from apps.library.immich import ImmichClient, ImmichError
from apps.library.models import Clip, Video
from apps.library.storage_paths import (
    build_originals_relative_path,
    to_absolute_storage_path,
)
from apps.pipeline.enqueue import STUB_DURATION_SECONDS, enqueue_probe_job


def _recorded_at(raw: str) -> datetime:
    """Immich reports ISO timestamps; fall back to now if one is unusable."""
    if raw:
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            parsed = None
        if parsed is not None:
            if timezone.is_naive(parsed):
                parsed = timezone.make_aware(parsed, UTC)
            return parsed
    return timezone.now()


DEFAULT_TAG = "Nakavid/add"
DEFAULT_IMPORTED_TAG = "Nakavid/imported"


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
        parser.add_argument("--class-name", required=True)
        parser.add_argument("--theme", required=True)
        parser.add_argument("--type", choices=["long", "short"], default="long")
        parser.add_argument("--user", help="Username to attribute the ingest to.")
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="List what would be pulled without downloading or writing rows.",
        )
        parser.add_argument("--limit", type=int, default=0, help="Stop after this many new assets.")

    def _resolve_user(self, username: str | None):
        model = get_user_model()
        if username:
            try:
                return model.objects.get(username=username)
            except model.DoesNotExist as exc:
                raise CommandError(f"No user named {username!r}") from exc
        superusers = list(model.objects.filter(is_superuser=True).order_by("id")[:2])
        if len(superusers) == 1:
            return superusers[0]
        raise CommandError("Pass --user: there is not exactly one superuser to attribute this to")

    def handle(self, *args, **options) -> None:
        dry_run: bool = options["dry_run"]
        user = self._resolve_user(options["user"])
        video_type = Video.VideoType.TYPE_A if options["type"] == "long" else Video.VideoType.TYPE_B
        orientation = (
            Video.Orientation.LANDSCAPE
            if video_type == Video.VideoType.TYPE_A
            else Video.Orientation.MIXED
        )
        storage_root = Path(settings.NAKAVID_STORAGE_ROOT)

        try:
            client = ImmichClient()
            tag = client.tag_named(options["tag"])
            assets = client.tagged_assets(str(tag["id"]))
        except ImmichError as exc:
            raise CommandError(str(exc)) from exc

        videos = [asset for asset in assets if asset.is_video]
        self.stdout.write(
            f"tag {options['tag']!r}: {len(assets)} asset(s), {len(videos)} video(s)"
        )

        already = set(
            Video.objects.exclude(immich_asset_id="").values_list("immich_asset_id", flat=True)
        )
        pulled = skipped = 0
        imported_asset_ids: list[str] = []
        for asset in videos:
            if asset.id in already:
                skipped += 1
                continue
            if options["limit"] and pulled >= options["limit"]:
                break

            recorded_at = _recorded_at(asset.created_at)
            relative_path = build_originals_relative_path(
                recorded_at=recorded_at,
                class_name=options["class_name"],
                theme=options["theme"],
                filename=get_valid_filename(asset.original_file_name),
            )
            storage_path = to_absolute_storage_path(storage_root, relative_path)

            self.stdout.write(
                f"{'would pull' if dry_run else 'pull '} {asset.original_file_name} "
                f"({recorded_at.date()}) -> {relative_path}"
            )
            if dry_run:
                pulled += 1
                continue

            try:
                client.download_asset(asset.id, storage_root / relative_path)
            except ImmichError as exc:
                self.stderr.write(f"  failed: {exc}")
                continue

            with transaction.atomic():
                video = Video.objects.create(
                    title=Path(asset.original_file_name).stem,
                    source_path=storage_path,
                    video_type=video_type,
                    orientation=orientation,
                    class_name=options["class_name"],
                    theme=options["theme"],
                    recorded_at=recorded_at,
                    duration_seconds=STUB_DURATION_SECONDS,
                    is_private=True,
                    immich_asset_id=asset.id,
                    created_by=user,
                )
                if video_type == Video.VideoType.TYPE_B:
                    Clip.objects.create(
                        video=video,
                        storage_path=storage_path,
                        start_seconds=0,
                        end_seconds=STUB_DURATION_SECONDS,
                        created_by=user,
                    )
                enqueue_probe_job(video=video)
            pulled += 1
            imported_asset_ids.append(asset.id)

        verb = "would pull" if dry_run else "pulled"
        self.stdout.write(f"{verb} {pulled} video(s), skipped {skipped} already in the library")

        if imported_asset_ids:
            self._retag_imported(
                client, options["imported_tag"], str(tag["id"]), imported_asset_ids
            )

    def _retag_imported(
        self, client: ImmichClient, imported_tag_name: str, source_tag_id: str, asset_ids: list[str]
    ) -> None:
        """Move freshly-imported assets from the source tag to the imported one.

        Best-effort: the videos are already safely in NakaVid by this point,
        so a hiccup here is a warning, not a reason to fail the whole run.
        """
        try:
            imported_tag = client.upsert_tag(imported_tag_name)
            client.tag_assets(str(imported_tag["id"]), asset_ids)
            client.untag_assets(source_tag_id, asset_ids)
        except ImmichError as exc:
            self.stderr.write(
                f"warning: imported {len(asset_ids)} video(s) but could not retag them in "
                f"Immich ({imported_tag_name!r}): {exc}"
            )
            return
        self.stdout.write(f"retagged {len(asset_ids)} video(s) as {imported_tag_name!r} in Immich")
