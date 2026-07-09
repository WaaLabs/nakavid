from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path


class CombineExportError(RuntimeError):
    """Raised when combine export cannot complete."""


def run_ffmpeg_concat(*, input_paths: list[Path], target_path: Path) -> None:
    if not input_paths:
        raise CombineExportError("Combine has no clip files to concat")

    target_path.parent.mkdir(parents=True, exist_ok=True)
    list_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            suffix=".txt",
            delete=False,
            encoding="utf-8",
        ) as list_file:
            for input_path in input_paths:
                escaped = str(input_path).replace("'", "'\\''")
                list_file.write(f"file '{escaped}'\n")
            list_path = Path(list_file.name)

        command = [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(list_path),
            "-c",
            "copy",
            str(target_path),
        ]
        try:
            subprocess.run(command, check=True, capture_output=True, text=True)
        except subprocess.CalledProcessError as exc:
            message = exc.stderr.strip() or "ffmpeg concat failed"
            raise CombineExportError(message) from exc
    finally:
        if list_path is not None:
            list_path.unlink(missing_ok=True)
