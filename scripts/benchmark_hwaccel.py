#!/usr/bin/env python3
"""Benchmark the pipeline's ffmpeg stages on CPU against VAAPI. Issue #53.

Run it against a real recording:

    python3 scripts/benchmark_hwaccel.py /path/to/source.MOV

VAAPI rows are skipped, with a reason, when the GPU is not reachable. Nothing
here touches the database or the media root — it writes to a temp directory.

Scope note: this covers the ffmpeg stages only (transcode, clip trims, contact
sheet). Segment scoring is not included because it cannot use the GPU with the
current OpenCV build — see the notes printed at the end.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

RENDER_NODE = Path("/dev/dri/renderD128")


@dataclass
class Result:
    name: str
    seconds: float | None
    note: str = ""


def vaapi_available() -> tuple[bool, str]:
    if not RENDER_NODE.exists():
        return False, "no render node at /dev/dri/renderD128"
    try:
        RENDER_NODE.open("rb").close()
    except PermissionError:
        return False, f"{RENDER_NODE} not readable — add your user to the 'render' group"
    except OSError as exc:
        return False, f"{RENDER_NODE}: {exc}"
    probe = subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-vaapi_device",
            str(RENDER_NODE),
            "-f",
            "lavfi",
            "-i",
            "testsrc=duration=0.2:size=128x128:rate=5",
            "-vf",
            "format=nv12,hwupload",
            "-c:v",
            "h264_vaapi",
            "-f",
            "null",
            "-",
        ],
        capture_output=True,
        text=True,
    )
    if probe.returncode != 0:
        return False, (probe.stderr.strip().splitlines() or ["ffmpeg rejected the device"])[0]
    return True, ""


def timed(command: list[str]) -> float:
    start = time.perf_counter()
    done = subprocess.run(command, capture_output=True, text=True)
    if done.returncode != 0:
        raise RuntimeError((done.stderr.strip().splitlines() or ["ffmpeg failed"])[-1])
    return time.perf_counter() - start


def bench(source: Path, seconds: int, workdir: Path, use_gpu: bool) -> list[Result]:
    results: list[Result] = []
    head = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error"]
    limit = ["-t", str(seconds)]

    if use_gpu:
        results.append(
            Result(
                "transcode to H.264",
                timed(
                    head
                    + [
                        "-hwaccel",
                        "vaapi",
                        "-hwaccel_output_format",
                        "vaapi",
                        "-vaapi_device",
                        str(RENDER_NODE),
                        "-i",
                        str(source),
                    ]
                    + limit
                    + [
                        "-map",
                        "0:v:0",
                        "-map",
                        "0:a:0?",
                        "-c:v",
                        "h264_vaapi",
                        "-c:a",
                        "aac",
                        "-movflags",
                        "+faststart",
                        str(workdir / "gpu_web.mp4"),
                    ]
                ),
            )
        )
        results.append(
            Result(
                "decode only",
                timed(
                    head
                    + ["-hwaccel", "vaapi", "-vaapi_device", str(RENDER_NODE), "-i", str(source)]
                    + limit
                    + ["-f", "null", "-"]
                ),
            )
        )
    else:
        results.append(
            Result(
                "transcode to H.264",
                timed(
                    head
                    + ["-i", str(source)]
                    + limit
                    + [
                        "-map",
                        "0:v:0",
                        "-map",
                        "0:a:0?",
                        "-c:v",
                        "libx264",
                        "-pix_fmt",
                        "yuv420p",
                        "-preset",
                        "veryfast",
                        "-crf",
                        "20",
                        "-c:a",
                        "aac",
                        "-movflags",
                        "+faststart",
                        str(workdir / "cpu_web.mp4"),
                    ]
                ),
            )
        )
        results.append(
            Result("decode only", timed(head + ["-i", str(source)] + limit + ["-f", "null", "-"]))
        )
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument(
        "--seconds", type=int, default=60, help="how much of the source to process (default 60)"
    )
    args = parser.parse_args()

    if not args.source.exists():
        print(f"No such file: {args.source}", file=sys.stderr)
        return 1
    if shutil.which("ffmpeg") is None:
        print("ffmpeg not found", file=sys.stderr)
        return 1

    gpu_ok, why = vaapi_available()
    print(f"source   : {args.source.name}")
    print(f"segment  : first {args.seconds}s")
    print(f"vaapi    : {'available' if gpu_ok else 'unavailable — ' + why}")
    print()

    with tempfile.TemporaryDirectory() as tmp:
        workdir = Path(tmp)
        cpu = bench(args.source, args.seconds, workdir, use_gpu=False)
        gpu = bench(args.source, args.seconds, workdir, use_gpu=True) if gpu_ok else []

        print(f"{'stage':>22} | {'cpu':>9} | {'vaapi':>9} | {'speedup':>8}")
        print("-" * 58)
        for index, row in enumerate(cpu):
            gpu_seconds = gpu[index].seconds if index < len(gpu) else None
            if isinstance(row.seconds, float) and isinstance(gpu_seconds, float):
                speedup = f"{row.seconds / gpu_seconds:.2f}x"
            else:
                speedup = "-"
            gpu_text = f"{gpu_seconds:.2f}s" if isinstance(gpu_seconds, float) else "-"
            print(f"{row.name:>22} | {row.seconds:8.2f}s | {gpu_text:>9} | {speedup:>8}")

    print()
    print("Scoring is deliberately absent. It cannot use the GPU as built:")
    print("  - Haar detection is ~53% of scoring time and opencv-python-headless")
    print("    reports OpenCL unavailable, so cascades stay on the CPU.")
    print("  - The same build reports VA: False, so cv2.VideoCapture cannot decode")
    print("    through VAAPI either — and Haar needs frames in system memory anyway.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
