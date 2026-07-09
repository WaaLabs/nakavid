import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import { DragCombine } from "./drag-combine";
import type { CombineClip, DragCombineProps } from "./types";
import "./drag-combine.css";

function parseClips(raw: string | null): CombineClip[] {
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
    const durationSeconds = Number(record.durationSeconds);
    const highlightScore = Number(record.highlightScore ?? 0);
    const label = typeof record.label === "string" ? record.label : `Clip ${id}`;
    const streamUrl =
      typeof record.streamUrl === "string" ? record.streamUrl : "";
    if (
      !Number.isFinite(id) ||
      !Number.isFinite(durationSeconds) ||
      streamUrl === ""
    ) {
      throw new Error(`Invalid clip payload at index ${index}`);
    }
    return {
      id,
      label,
      streamUrl,
      durationSeconds: Number.isFinite(durationSeconds) ? durationSeconds : 0,
      highlightScore: Number.isFinite(highlightScore) ? highlightScore : 0,
    };
  });
}

function readProps(root: HTMLElement): DragCombineProps {
  const submitUrl = root.dataset.submitUrl ?? "";
  const csrfToken = root.dataset.csrfToken ?? "";
  if (submitUrl === "" || csrfToken === "") {
    throw new Error("Combine Builder island requires submit URL and CSRF token");
  }
  return {
    clips: parseClips(root.dataset.clips ?? null),
    submitUrl,
    csrfToken,
  };
}

function mount(): void {
  const root = document.getElementById("combine-builder-island");
  if (root === null) {
    return;
  }
  const props = readProps(root);
  createRoot(root).render(
    <StrictMode>
      <DragCombine {...props} />
    </StrictMode>,
  );
}

mount();
