from __future__ import annotations

import json
import shutil
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

from django.utils import timezone
from django.utils.text import get_valid_filename

from apps.library.storage_paths import build_originals_relative_path, to_absolute_storage_path
from apps.library.video_metadata import probe_creation_time

UPLOADS_DIRNAME = ".uploads"
META_FILENAME = "meta.json"
DATA_FILENAME = "data.part"
TUS_VERSION = "1.0.0"
TUS_RESUMABLE_HEADER = "1.0.0"


class ResumableUploadError(Exception):
    def __init__(self, message: str, *, status_code: int = 400) -> None:
        super().__init__(message)
        self.status_code = status_code


@dataclass(frozen=True)
class UploadMetadata:
    upload_id: str
    user_id: int
    class_name: str
    theme: str
    filename: str
    upload_length: int

    @property
    def safe_filename(self) -> str:
        return get_valid_filename(self.filename)


def uploads_root(storage_root: Path) -> Path:
    return storage_root / UPLOADS_DIRNAME


def upload_dir(storage_root: Path, upload_id: str) -> Path:
    return uploads_root(storage_root) / upload_id


def _meta_path(storage_root: Path, upload_id: str) -> Path:
    return upload_dir(storage_root, upload_id) / META_FILENAME


def _data_path(storage_root: Path, upload_id: str) -> Path:
    return upload_dir(storage_root, upload_id) / DATA_FILENAME


def create_upload(
    *,
    storage_root: Path,
    user_id: int,
    class_name: str,
    theme: str,
    filename: str,
    upload_length: int,
) -> UploadMetadata:
    if upload_length <= 0:
        raise ResumableUploadError("Upload-Length must be greater than zero.")

    upload_id = uuid.uuid4().hex
    metadata = UploadMetadata(
        upload_id=upload_id,
        user_id=user_id,
        class_name=class_name,
        theme=theme,
        filename=filename,
        upload_length=upload_length,
    )
    destination = upload_dir(storage_root, upload_id)
    destination.mkdir(parents=True, exist_ok=False)
    _data_path(storage_root, upload_id).touch()
    _meta_path(storage_root, upload_id).write_text(
        json.dumps(asdict(metadata), indent=2),
        encoding="utf-8",
    )
    return metadata


def load_metadata(storage_root: Path, upload_id: str) -> UploadMetadata:
    meta_file = _meta_path(storage_root, upload_id)
    if not meta_file.is_file():
        raise ResumableUploadError("Upload not found.", status_code=404)
    payload = json.loads(meta_file.read_text(encoding="utf-8"))
    return UploadMetadata(**payload)


def current_offset(storage_root: Path, upload_id: str) -> int:
    data_file = _data_path(storage_root, upload_id)
    if not data_file.is_file():
        raise ResumableUploadError("Upload not found.", status_code=404)
    return data_file.stat().st_size


def append_chunk(
    *,
    storage_root: Path,
    upload_id: str,
    user_id: int,
    offset: int,
    chunk: bytes,
) -> int:
    metadata = load_metadata(storage_root, upload_id)
    if metadata.user_id != user_id:
        raise ResumableUploadError("Upload not found.", status_code=404)

    data_file = _data_path(storage_root, upload_id)
    current_size = data_file.stat().st_size
    if offset != current_size:
        raise ResumableUploadError(
            f"Upload-Offset {offset} does not match current size {current_size}.",
            status_code=409,
        )

    new_size = offset + len(chunk)
    if new_size > metadata.upload_length:
        raise ResumableUploadError("Chunk exceeds declared Upload-Length.")

    with data_file.open("ab") as handle:
        handle.write(chunk)

    return new_size


def finalize_upload(
    *, storage_root: Path, upload_id: str, user_id: int
) -> tuple[Path, str, datetime]:
    metadata = load_metadata(storage_root, upload_id)
    if metadata.user_id != user_id:
        raise ResumableUploadError("Upload not found.", status_code=404)

    data_file = _data_path(storage_root, upload_id)
    if data_file.stat().st_size != metadata.upload_length:
        raise ResumableUploadError("Upload is incomplete.")

    # Only now, with the file fully assembled, can its own metadata be read.
    # No other date is available for an upload — falls back to now, not a
    # file mtime, which for a just-written file means the same thing anyway.
    recorded_at = probe_creation_time(data_file) or timezone.now()

    relative_path = build_originals_relative_path(
        recorded_at=recorded_at, filename=metadata.safe_filename
    )
    destination = storage_root / relative_path
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(data_file), destination)

    upload_directory = upload_dir(storage_root, upload_id)
    shutil.rmtree(upload_directory)

    source_path = to_absolute_storage_path(storage_root, relative_path)
    return destination, source_path, recorded_at
