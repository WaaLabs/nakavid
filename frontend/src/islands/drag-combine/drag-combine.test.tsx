import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { DragCombine } from "./drag-combine";
import { undefinedClassNames } from "@/test/styles";
import type { CombineClip } from "./types";

afterEach(cleanup);

// Named rather than indexed: noUncheckedIndexedAccess makes CLIPS[0] optional.
const FIRST_CLIP: CombineClip = {
  id: 1,
  label: "lesson · 0:10–0:40",
  streamUrl: "/clips/1/stream/",
  durationSeconds: 30,
  highlightScore: 70,
};

const SECOND_CLIP: CombineClip = {
  id: 2,
  label: "lesson · 1:00–1:30",
  streamUrl: "/clips/2/stream/",
  durationSeconds: 30,
  highlightScore: 80,
};

const CLIPS: CombineClip[] = [FIRST_CLIP, SECOND_CLIP];

function renderBuilder(clips: CombineClip[] = CLIPS) {
  return render(
    <DragCombine clips={clips} submitUrl="/combine-builder/submit/" csrfToken="token" />,
  );
}

/** Drag a clip from the available list into the combine list. */
function dragIntoCombine(container: HTMLElement, label: string) {
  const source = screen.getByText(label).closest("li");
  if (source === null) {
    throw new Error(`no clip row for ${label}`);
  }
  const dataTransfer = {
    data: {} as Record<string, string>,
    setData(key: string, value: string) {
      this.data[key] = value;
    },
    getData(key: string) {
      return this.data[key] ?? "";
    },
    effectAllowed: "move",
    dropEffect: "move",
  };
  fireEvent.dragStart(source, { dataTransfer });
  const target = container.querySelectorAll(".drag-combine__list")[1];
  if (target === undefined) {
    throw new Error("combine list not rendered");
  }
  fireEvent.dragOver(target, { dataTransfer });
  fireEvent.drop(target, { dataTransfer });
}

describe("DragCombine", () => {
  it("uses only class names some stylesheet defines", () => {
    const { container } = renderBuilder();

    expect(undefinedClassNames(container)).toEqual([]);
  });

  it("starts with an empty combine and a disabled export", () => {
    renderBuilder();

    expect(screen.getByRole("button", { name: /export combine/i })).toHaveProperty(
      "disabled",
      true,
    );
  });

  it("adds a dragged clip to the combine and totals the duration", () => {
    const { container } = renderBuilder();

    dragIntoCombine(container, FIRST_CLIP.label);

    const combineList = screen.getByLabelText("Combine list");
    expect(combineList.textContent).toContain("1 clip");
    expect(combineList.textContent).toContain("0:30");
  });

  it("removes a clip from the combine", () => {
    const { container } = renderBuilder();
    dragIntoCombine(container, FIRST_CLIP.label);

    fireEvent.click(screen.getByRole("button", { name: `Remove ${FIRST_CLIP.label}` }));

    expect(screen.getByLabelText("Combine list").textContent).toContain("0 clips");
  });

  it("refuses to export without a title", () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    const { container } = renderBuilder();
    dragIntoCombine(container, FIRST_CLIP.label);

    // The button guards it, so the request must never be attempted.
    expect(screen.getByRole("button", { name: /export combine/i })).toHaveProperty(
      "disabled",
      true,
    );
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("posts the ordered clip ids and reports the queued job", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ combineId: 12, jobId: 34, queueStatusUrl: "/queue-status/" }),
    });
    vi.stubGlobal("fetch", fetchMock);

    const { container } = renderBuilder();
    dragIntoCombine(container, SECOND_CLIP.label);
    dragIntoCombine(container, FIRST_CLIP.label);
    fireEvent.change(screen.getByLabelText(/combine title/i), {
      target: { value: "Week 1" },
    });

    fireEvent.click(screen.getByRole("button", { name: /export combine/i }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    const call = fetchMock.mock.calls[0];
    if (call === undefined) {
      throw new Error("fetch was not called");
    }
    const [url, options] = call;
    expect(url).toBe("/combine-builder/submit/");
    expect(options.headers["X-CSRFToken"]).toBe("token");
    // Order is the order they were dragged in, not the order they were listed.
    expect(JSON.parse(options.body)).toEqual({ title: "Week 1", clip_ids: [2, 1] });

    await screen.findByText(/Combine #12 queued as job #34/);
  });

  it("surfaces server-side validation errors", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: false,
        json: async () => ({ errors: { title: ["That title is already taken."] } }),
      }),
    );

    const { container } = renderBuilder();
    dragIntoCombine(container, FIRST_CLIP.label);
    fireEvent.change(screen.getByLabelText(/combine title/i), {
      target: { value: "Week 1" },
    });
    fireEvent.click(screen.getByRole("button", { name: /export combine/i }));

    await screen.findByText(/That title is already taken./);
  });

  it("reports a network failure rather than failing silently", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("offline")));

    const { container } = renderBuilder();
    dragIntoCombine(container, FIRST_CLIP.label);
    fireEvent.change(screen.getByLabelText(/combine title/i), {
      target: { value: "Week 1" },
    });
    fireEvent.click(screen.getByRole("button", { name: /export combine/i }));

    await screen.findByText(/network error/i);
  });

  it("says so when no clips match the filter", () => {
    renderBuilder([]);

    expect(screen.getByText(/no clips match/i)).toBeTruthy();
  });
});
