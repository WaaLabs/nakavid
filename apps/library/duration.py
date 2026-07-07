from __future__ import annotations

import json
import subprocess
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path


def probe_duration_seconds(file_path: Path) -> int:
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "json",
                str(file_path),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
    except (FileNotFoundError, subprocess.CalledProcessError):
        return 1

    payload = json.loads(result.stdout)
    duration = Decimal(str(payload["format"]["duration"]))
    seconds = int(duration.to_integral_value(rounding=ROUND_HALF_UP))
    return max(seconds, 1)
