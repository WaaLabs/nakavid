"""The actual Immich pull, shared by the CLI command and the background job.

`manage.py ingest_immich` and the web-triggered "Scan Immich" button (a
queued Job.JobType.INGEST, handled in apps/pipeline/handlers.py) both need to
run this same sequence: find the tag, list its videos, download whatever
isn't already in the library, queue each for probing, then move the tag from
"add" to "imported" in Immich. This module is that sequence, with no CLI or
Job-specific code in it — callers supply where output lines go (or don't).
"""

from __future__ import annotations

import shutil
from datetime import UTC, datetime
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone
from django.utils.text import get_valid_filename

from apps.library.immich import ImmichClient, ImmichError
from apps.library.models import Clip, Video
from apps.library.storage_paths import (
    build_originals_relative_path,
    build_staging_relative_path,
    to_absolute_storage_path,
)
from apps.library.video_metadata import probe_creation_time
from apps.pipeline.enqueue import STUB_DURATION_SECONDS, enqueue_probe_job

DEFAULT_TAG = "Nakavid/add"
DEFAULT_IMPORTED_TAG = "Nakavid/imported"


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


def _noop(_line: str) -> None:
    pass


def resolve_default_user():
    """The one superuser to attribute an ingest to, when nothing more specific
    says who — the CLI command's fallback when --user is omitted, and the
    only option a web-triggered scan has (there's no --user equivalent
    there). Same message either way: fix it by naming a user explicitly, or
    by there being only one superuser in the first place."""
    model = get_user_model()
    superusers = list(model.objects.filter(is_superuser=True).order_by("id")[:2])
    if len(superusers) == 1:
        return superusers[0]
    raise ImmichError("Pass --user: there is not exactly one superuser to attribute this to")


def run_immich_ingest(
    *,
    user,
    tag: str = DEFAULT_TAG,
    imported_tag: str = DEFAULT_IMPORTED_TAG,
    video_type: str = Video.VideoType.TYPE_A,
    orientation: str = Video.Orientation.LANDSCAPE,
    class_name: str = "",
    theme: str = "",
    dry_run: bool = False,
    limit: int = 0,
    write_out=_noop,
    write_err=_noop,
) -> None:
    """Pull every video tagged `tag` that isn't already in the library.

    Raises ImmichError on anything that stops the run outright (bad
    credentials, tag not found, ...). A single asset's download failing does
    not — it's logged via write_err and skipped, same as it always has been.
    """
    storage_root = Path(settings.NAKAVID_STORAGE_ROOT)

    client = ImmichClient()
    immich_tag = client.tag_named(tag)
    assets = client.tagged_assets(str(immich_tag["id"]))

    videos = [asset for asset in assets if asset.is_video]
    write_out(f"tag {tag!r}: {len(assets)} asset(s), {len(videos)} video(s)")

    already = set(
        Video.objects.exclude(immich_asset_id="").values_list("immich_asset_id", flat=True)
    )
    pulled = skipped = 0
    imported_asset_ids: list[str] = []
    for asset in videos:
        if asset.id in already:
            skipped += 1
            continue
        if limit and pulled >= limit:
            break

        filename = get_valid_filename(asset.original_file_name)
        if dry_run:
            # Nothing is downloaded, so the file's own metadata isn't
            # readable yet — report Immich's date as an estimate. The real
            # run below reads the file itself and may land it a day either
            # side of this if Immich's own date disagrees.
            estimated_at = _recorded_at(asset.created_at)
            relative_path = build_originals_relative_path(
                recorded_at=estimated_at, filename=filename
            )
            write_out(
                f"would pull {asset.original_file_name} "
                f"(~{estimated_at.date()}) -> {relative_path}"
            )
            pulled += 1
            continue

        staging_path = storage_root / build_staging_relative_path(asset.id, filename)
        try:
            client.download_asset(asset.id, staging_path)
        except ImmichError as exc:
            write_err(f"  failed: {exc}")
            continue

        # The file's own creation_time tag is the trustworthy source; Immich's
        # asset metadata is a fallback for footage that has none.
        recorded_at = probe_creation_time(staging_path) or _recorded_at(asset.created_at)
        relative_path = build_originals_relative_path(recorded_at=recorded_at, filename=filename)
        storage_path = to_absolute_storage_path(storage_root, relative_path)
        destination = storage_root / relative_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(staging_path), destination)
        try:
            staging_path.parent.rmdir()
        except OSError:
            pass

        write_out(f"pull  {asset.original_file_name} ({recorded_at.date()}) -> {relative_path}")

        with transaction.atomic():
            video = Video.objects.create(
                title=Path(asset.original_file_name).stem,
                source_path=storage_path,
                video_type=video_type,
                orientation=orientation,
                class_name=class_name,
                theme=theme,
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
    write_out(f"{verb} {pulled} video(s), skipped {skipped} already in the library")

    if imported_asset_ids:
        _retag_imported(
            client, imported_tag, str(immich_tag["id"]), imported_asset_ids, write_out, write_err
        )


def _retag_imported(
    client: ImmichClient,
    imported_tag_name: str,
    source_tag_id: str,
    asset_ids: list[str],
    write_out,
    write_err,
) -> None:
    """Move freshly-imported assets from the source tag to the imported one.

    Best-effort: the videos are already safely in NakaVid by this point, so a
    hiccup here is a warning, not a reason to fail the whole run.
    """
    try:
        imported_tag = client.upsert_tag(imported_tag_name)
        client.tag_assets(str(imported_tag["id"]), asset_ids)
        client.untag_assets(source_tag_id, asset_ids)
    except ImmichError as exc:
        write_err(
            f"warning: imported {len(asset_ids)} video(s) but could not retag them in "
            f"Immich ({imported_tag_name!r}): {exc}"
        )
        return
    write_out(f"retagged {len(asset_ids)} video(s) as {imported_tag_name!r} in Immich")
