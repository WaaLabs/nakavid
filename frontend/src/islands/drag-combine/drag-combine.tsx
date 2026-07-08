import type { DragEvent } from "react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import type { CombineClip, DragCombineProps, SubmitResponse } from "./types";
import { cn } from "@/utils/cn";

const DRAG_MIME = "application/x-nakavid-clip-id";

type DropTarget = "pool" | "combine" | null;

function formatDuration(totalSeconds: number): string {
  const safe = Math.max(0, Math.round(totalSeconds));
  const minutes = Math.floor(safe / 60);
  const seconds = safe % 60;
  return `${minutes}:${String(seconds).padStart(2, "0")}`;
}

function readDraggedClipId(event: DragEvent): number | null {
  const raw =
    event.dataTransfer.getData(DRAG_MIME) ||
    event.dataTransfer.getData("text/plain");
  const parsed = Number(raw);
  return Number.isFinite(parsed) ? parsed : null;
}

function moveItem<T>(items: T[], fromIndex: number, toIndex: number): T[] {
  if (fromIndex === toIndex || fromIndex < 0 || toIndex < 0) {
    return items;
  }
  const next = [...items];
  const [item] = next.splice(fromIndex, 1);
  if (item === undefined) {
    return items;
  }
  next.splice(toIndex, 0, item);
  return next;
}

type ClipRowProps = {
  clip: CombineClip;
  draggable: boolean;
  onRemove?: () => void;
  onDragStart: (event: DragEvent, clipId: number) => void;
  onDragOver: (event: DragEvent) => void;
  onDrop: (event: DragEvent) => void;
};

function ClipRow({
  clip,
  draggable,
  onRemove,
  onDragStart,
  onDragOver,
  onDrop,
}: ClipRowProps) {
  return (
    <li
      className="drag-combine__item"
      draggable={draggable}
      onDragStart={(event) => {
        onDragStart(event, clip.id);
      }}
      onDragOver={onDragOver}
      onDrop={onDrop}
    >
      <div>
        <div className="drag-combine__item-label">{clip.label}</div>
        <div className="drag-combine__item-meta">
          {formatDuration(clip.durationSeconds)}
        </div>
      </div>
      {onRemove ? (
        <div className="drag-combine__item-actions">
          <button
            type="button"
            className="drag-combine__button"
            aria-label={`Remove ${clip.label}`}
            onClick={onRemove}
          >
            Remove
          </button>
        </div>
      ) : null}
    </li>
  );
}

export function DragCombine({ clips, submitUrl, csrfToken }: DragCombineProps) {
  const previewRef = useRef<HTMLVideoElement>(null);
  const [orderedClipIds, setOrderedClipIds] = useState<number[]>([]);
  const [previewIndex, setPreviewIndex] = useState<number | null>(null);
  const [previewPlaying, setPreviewPlaying] = useState(false);
  const [title, setTitle] = useState("");
  const [statusMessage, setStatusMessage] = useState<string | null>(null);
  const [statusTone, setStatusTone] = useState<"error" | "success" | null>(
    null,
  );
  const [submitting, setSubmitting] = useState(false);
  const [dropTarget, setDropTarget] = useState<DropTarget>(null);

  const clipsById = useMemo(() => {
    const map = new Map<number, CombineClip>();
    for (const clip of clips) {
      map.set(clip.id, clip);
    }
    return map;
  }, [clips]);

  const availableClips = useMemo(
    () => clips.filter((clip) => !orderedClipIds.includes(clip.id)),
    [clips, orderedClipIds],
  );

  const orderedClips = useMemo(
    () =>
      orderedClipIds
        .map((clipId) => clipsById.get(clipId))
        .filter((clip): clip is CombineClip => clip !== undefined),
    [clipsById, orderedClipIds],
  );

  const totalDurationSeconds = useMemo(
    () =>
      orderedClips.reduce(
        (total, clip) => total + Math.max(clip.durationSeconds, 0),
        0,
      ),
    [orderedClips],
  );

  const addClipToCombine = useCallback((clipId: number) => {
    setOrderedClipIds((current) =>
      current.includes(clipId) ? current : [...current, clipId],
    );
    setStatusMessage(null);
    setStatusTone(null);
  }, []);

  const removeClipFromCombine = useCallback((clipId: number) => {
    setOrderedClipIds((current) => current.filter((id) => id !== clipId));
    setPreviewIndex(null);
    setPreviewPlaying(false);
    setStatusMessage(null);
    setStatusTone(null);
  }, []);

  const handleDragStart = useCallback(
    (event: DragEvent, clipId: number) => {
      event.dataTransfer.setData(DRAG_MIME, String(clipId));
      event.dataTransfer.setData("text/plain", String(clipId));
      event.dataTransfer.effectAllowed = "move";
    },
    [],
  );

  const handleDropOnCombine = useCallback(
    (event: DragEvent) => {
      event.preventDefault();
      setDropTarget(null);
      const clipId = readDraggedClipId(event);
      if (clipId === null) {
        return;
      }
      addClipToCombine(clipId);
    },
    [addClipToCombine],
  );

  const handleDropOnOrderedItem = useCallback(
    (event: DragEvent, targetIndex: number) => {
      event.preventDefault();
      event.stopPropagation();
      setDropTarget(null);
      const clipId = readDraggedClipId(event);
      if (clipId === null) {
        return;
      }
      setOrderedClipIds((current) => {
        const fromIndex = current.indexOf(clipId);
        if (fromIndex === -1) {
          const next = [...current];
          next.splice(targetIndex, 0, clipId);
          return next;
        }
        return moveItem(current, fromIndex, targetIndex);
      });
    },
    [],
  );

  const playClipAtIndex = useCallback(
    async (index: number) => {
      const clip = orderedClips[index];
      const player = previewRef.current;
      if (clip === undefined || player === null) {
        return;
      }
      setPreviewIndex(index);
      player.src = clip.streamUrl;
      player.load();
      try {
        await player.play();
        setPreviewPlaying(true);
      } catch {
        setPreviewPlaying(false);
      }
    },
    [orderedClips],
  );

  const startPreview = useCallback(async () => {
    if (orderedClips.length === 0) {
      return;
    }
    await playClipAtIndex(0);
  }, [orderedClips.length, playClipAtIndex]);

  useEffect(() => {
    const player = previewRef.current;
    if (player === null) {
      return;
    }

    const onEnded = (): void => {
      if (previewIndex === null) {
        setPreviewPlaying(false);
        return;
      }
      const nextIndex = previewIndex + 1;
      if (nextIndex >= orderedClips.length) {
        setPreviewPlaying(false);
        setPreviewIndex(null);
        return;
      }
      void playClipAtIndex(nextIndex);
    };

    player.addEventListener("ended", onEnded);
    return () => {
      player.removeEventListener("ended", onEnded);
    };
  }, [orderedClips.length, playClipAtIndex, previewIndex]);

  const submitCombine = async (): Promise<void> => {
    if (orderedClipIds.length === 0 || title.trim() === "") {
      setStatusTone("error");
      setStatusMessage("Enter a title and add at least one clip.");
      return;
    }

    setSubmitting(true);
    setStatusMessage(null);
    setStatusTone(null);

    try {
      const response = await fetch(submitUrl, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-CSRFToken": csrfToken,
        },
        body: JSON.stringify({
          title: title.trim(),
          clip_ids: orderedClipIds,
        }),
      });
      const payload: SubmitResponse | { errors: Record<string, string[]> } =
        await response.json();
      if (!response.ok) {
        const errors =
          "errors" in payload ? Object.values(payload.errors).flat() : [];
        setStatusTone("error");
        setStatusMessage(
          errors.length > 0 ? errors.join(" ") : "Submit failed.",
        );
        return;
      }
      const success = payload as SubmitResponse;
      setStatusTone("success");
      setStatusMessage(
        `Combine #${success.combineId} queued as job #${success.jobId}.`,
      );
      setOrderedClipIds([]);
      setPreviewIndex(null);
      setPreviewPlaying(false);
      setTitle("");
      const player = previewRef.current;
      if (player !== null) {
        player.removeAttribute("src");
        player.load();
      }
    } catch {
      setStatusTone("error");
      setStatusMessage("Submit failed — network error.");
    } finally {
      setSubmitting(false);
    }
  };

  const currentPreviewLabel =
    previewIndex !== null ? orderedClips[previewIndex]?.label : null;

  return (
    <div className={cn("drag-combine")}>
      <div className="drag-combine__layout">
        <section className="drag-combine__panel" aria-label="Available clips">
          <h2>Available clips</h2>
          <p className="drag-combine__hint">
            Drag clips into the combine list on the right.
          </p>
          {availableClips.length === 0 ? (
            <p className="drag-combine__empty">No clips match the current filter.</p>
          ) : (
            <ul className="drag-combine__list">
              {availableClips.map((clip) => (
                <ClipRow
                  key={clip.id}
                  clip={clip}
                  draggable
                  onDragStart={handleDragStart}
                  onDragOver={(event) => {
                    event.preventDefault();
                  }}
                  onDrop={(event) => {
                    event.preventDefault();
                  }}
                />
              ))}
            </ul>
          )}
        </section>

        <section className="drag-combine__panel" aria-label="Combine list">
          <h2>Combine list</h2>
          <p className="drag-combine__hint">
            {orderedClips.length} clip{orderedClips.length === 1 ? "" : "s"} ·{" "}
            {formatDuration(totalDurationSeconds)} total
          </p>
          <ul
            className={cn(
              "drag-combine__list",
              dropTarget === "combine" && "drag-combine__list--drop-target",
            )}
            onDragOver={(event) => {
              event.preventDefault();
              setDropTarget("combine");
            }}
            onDragLeave={() => {
              setDropTarget(null);
            }}
            onDrop={handleDropOnCombine}
          >
            {orderedClips.length === 0 ? (
              <li className="drag-combine__empty">Drop clips here to build a combine.</li>
            ) : (
              orderedClips.map((clip, index) => (
                <ClipRow
                  key={clip.id}
                  clip={clip}
                  draggable
                  onRemove={() => {
                    removeClipFromCombine(clip.id);
                  }}
                  onDragStart={handleDragStart}
                  onDragOver={(event) => {
                    event.preventDefault();
                  }}
                  onDrop={(event) => {
                    handleDropOnOrderedItem(event, index);
                  }}
                />
              ))
            )}
          </ul>
        </section>
      </div>

      <section className="drag-combine__preview" aria-label="Sequential preview">
        <h2>Preview</h2>
        <video ref={previewRef} controls preload="metadata" />
        <div className="drag-combine__preview-meta">
          <span>
            {previewPlaying
              ? `Playing clip ${(previewIndex ?? 0) + 1} of ${orderedClips.length}`
              : "Sequential playback plays each clip in list order."}
          </span>
          {currentPreviewLabel ? <span>{currentPreviewLabel}</span> : null}
        </div>
        <button
          type="button"
          className="drag-combine__button drag-combine__button--primary"
          disabled={orderedClips.length === 0}
          onClick={() => {
            void startPreview();
          }}
        >
          Play preview
        </button>
      </section>

      <section className="drag-combine__submit" aria-label="Export combine">
        <label htmlFor="combine-title">Combine title</label>
        <input
          id="combine-title"
          type="text"
          value={title}
          maxLength={255}
          placeholder="Week 1 highlights"
          onChange={(event) => {
            setTitle(event.target.value);
          }}
        />
        <button
          type="button"
          className="drag-combine__button drag-combine__button--primary"
          disabled={submitting || orderedClips.length === 0 || title.trim() === ""}
          onClick={() => {
            void submitCombine();
          }}
        >
          {submitting ? "Submitting…" : "Export combine"}
        </button>
        {statusMessage ? (
          <p
            className={cn(
              "drag-combine__status",
              statusTone === "error" && "drag-combine__status--error",
              statusTone === "success" && "drag-combine__status--success",
            )}
          >
            {statusMessage}
          </p>
        ) : null}
      </section>
    </div>
  );
}
