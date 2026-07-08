import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import { TimelineScrub } from "./timeline-scrub";
import type { TimelineClip, TimelineProps } from "./types";
import "./timeline-scrub.css";

function parseNumber(value: string | null, fallback: number): number {
  if (value === null || value.trim() === "") {
    return fallback;
  }
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : fallback;
}

function parseClips(raw: string | null): TimelineClip[] {
  if (raw === null || raw.trim() === "") {
    return [];
  }
  const parsed: unknown = JSON.parse(raw);
  if (!Array.isArray(parsed)) {
    throw new Error("data-clips must be a JSON array");
  }
  return parsed.map((item, index) => {
    if (typeof item !== "object" || item === null) {
      throw new Error(`Invalid clip at index ${index}`);
    }
    const record = item as Record<string, unknown>;
    const id = Number(record.id);
    const startSeconds = Number(record.startSeconds);
    const endSeconds = Number(record.endSeconds);
    const highlightScore = Number(record.highlightScore ?? 0);
    const label = typeof record.label === "string" ? record.label : `Clip ${id}`;
    if (
      !Number.isFinite(id) ||
      !Number.isFinite(startSeconds) ||
      !Number.isFinite(endSeconds)
    ) {
      throw new Error(`Invalid clip numbers at index ${index}`);
    }
    return {
      id,
      startSeconds,
      endSeconds,
      highlightScore: Number.isFinite(highlightScore) ? highlightScore : 0,
      label,
    };
  });
}

function readProps(root: HTMLElement): TimelineProps {
  return {
    durationSeconds: parseNumber(root.dataset.durationSeconds ?? null, 0),
    clips: parseClips(root.dataset.clips ?? null),
    playerSelector: root.dataset.playerSelector ?? "#lesson-source-player",
  };
}

function mount(): void {
  const root = document.getElementById("lesson-timeline-island");
  if (root === null) {
    return;
  }
  const props = readProps(root);
  createRoot(root).render(
    <StrictMode>
      <TimelineScrub {...props} />
    </StrictMode>,
  );
}

mount();
