import { useEffect, useMemo, useState } from "react";

import { ClipSegment, scoreToTone } from "./clip-segment";
import type { TimelineClip, TimelineProps } from "./types";
import { cn } from "@/utils/cn";

function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value));
}

function formatTimecode(totalSeconds: number): string {
  const safe = Math.max(0, Math.floor(totalSeconds));
  const hours = Math.floor(safe / 3600);
  const minutes = Math.floor((safe % 3600) / 60);
  const seconds = safe % 60;
  if (hours > 0) {
    return `${hours}:${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}`;
  }
  return `${minutes}:${String(seconds).padStart(2, "0")}`;
}

function seekPlayer(player: HTMLVideoElement | null, seconds: number): void {
  if (player === null) {
    return;
  }
  const duration = Number.isFinite(player.duration) ? player.duration : seconds;
  player.currentTime = clamp(seconds, 0, Math.max(duration, 0));
}

export function TimelineScrub({
  durationSeconds,
  clips,
  playerSelector,
}: TimelineProps) {
  const [activeClipId, setActiveClipId] = useState<number | null>(null);
  const [playheadSeconds, setPlayheadSeconds] = useState(0);

  const orderedClips = useMemo(
    () =>
      [...clips].sort(
        (left, right) => left.startSeconds - right.startSeconds || left.id - right.id,
      ),
    [clips],
  );

  const safeDuration = Math.max(durationSeconds, 1);

  useEffect(() => {
    const player = document.querySelector(playerSelector);
    if (!(player instanceof HTMLVideoElement)) {
      return;
    }

    const onTimeUpdate = (): void => {
      setPlayheadSeconds(player.currentTime);
    };
    player.addEventListener("timeupdate", onTimeUpdate);
    player.addEventListener("seeked", onTimeUpdate);
    return () => {
      player.removeEventListener("timeupdate", onTimeUpdate);
      player.removeEventListener("seeked", onTimeUpdate);
    };
  }, [playerSelector]);

  const selectClip = (clip: TimelineClip): void => {
    const player = document.querySelector(playerSelector);
    seekPlayer(player instanceof HTMLVideoElement ? player : null, clip.startSeconds);
    setActiveClipId(clip.id);
    setPlayheadSeconds(clip.startSeconds);
  };

  const scrubToRatio = (clientX: number, track: HTMLElement): void => {
    const rect = track.getBoundingClientRect();
    if (rect.width <= 0) {
      return;
    }
    const ratio = clamp((clientX - rect.left) / rect.width, 0, 1);
    const seconds = ratio * safeDuration;
    const player = document.querySelector(playerSelector);
    seekPlayer(player instanceof HTMLVideoElement ? player : null, seconds);
    setPlayheadSeconds(seconds);

    const hit = orderedClips.find(
      (clip) => seconds >= clip.startSeconds && seconds <= clip.endSeconds,
    );
    setActiveClipId(hit?.id ?? null);
  };

  return (
    <section className={cn("timeline-scrub")} aria-label="Clip timeline">
      <div className="timeline-scrub__meta">
        <span>Playhead {formatTimecode(playheadSeconds)}</span>
        <span>Duration {formatTimecode(safeDuration)}</span>
        <span>{orderedClips.length} clips</span>
      </div>
      <div
        className="timeline-scrub__track"
        role="slider"
        tabIndex={0}
        aria-valuemin={0}
        aria-valuemax={safeDuration}
        aria-valuenow={Math.round(playheadSeconds)}
        aria-label="Source timeline"
        onClick={(event) => {
          scrubToRatio(event.clientX, event.currentTarget);
        }}
        onKeyDown={(event) => {
          const step = event.shiftKey ? 10 : 1;
          if (event.key === "ArrowLeft" || event.key === "ArrowRight") {
            event.preventDefault();
            const delta = event.key === "ArrowLeft" ? -step : step;
            const next = clamp(playheadSeconds + delta, 0, safeDuration);
            const player = document.querySelector(playerSelector);
            seekPlayer(player instanceof HTMLVideoElement ? player : null, next);
            setPlayheadSeconds(next);
          }
        }}
      >
        {orderedClips.map((clip) => {
          const leftPercent = (clip.startSeconds / safeDuration) * 100;
          const widthPercent =
            ((clip.endSeconds - clip.startSeconds) / safeDuration) * 100;
          return (
            <ClipSegment
              key={clip.id}
              label={`${clip.label} · score ${clip.highlightScore}`}
              leftPercent={leftPercent}
              widthPercent={widthPercent}
              tone={scoreToTone(clip.highlightScore)}
              active={activeClipId === clip.id}
              onSelect={() => {
                selectClip(clip);
              }}
            />
          );
        })}
        <div
          className="timeline-scrub__playhead"
          style={{ left: `${(playheadSeconds / safeDuration) * 100}%` }}
          aria-hidden="true"
        />
      </div>
      {orderedClips.length === 0 ? (
        <p className="timeline-scrub__empty">No extracted clips for this video yet.</p>
      ) : null}
    </section>
  );
}
