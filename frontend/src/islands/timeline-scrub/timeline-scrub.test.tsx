import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { TimelineScrub } from "./timeline-scrub";
import { undefinedClassNames } from "@/test/styles";
import type { TimelineClip } from "./types";

afterEach(cleanup);

// Named rather than indexed: noUncheckedIndexedAccess makes CLIPS[0] optional.
const QUIET_CLIP: TimelineClip = {
  id: 7, startSeconds: 0, endSeconds: 30, highlightScore: 20, label: "0.0s–30.0s",
};
const MIDDLING_CLIP: TimelineClip = {
  id: 8, startSeconds: 300, endSeconds: 330, highlightScore: 55, label: "300.0s–330.0s",
};
const BEST_CLIP: TimelineClip = {
  id: 9, startSeconds: 900, endSeconds: 960, highlightScore: 90, label: "900.0s–960.0s",
};

const CLIPS: TimelineClip[] = [QUIET_CLIP, MIDDLING_CLIP, BEST_CLIP];

/** Indexing into the rendered bars, with the optionality checked once. */
function barAt(index: number): HTMLElement {
  const bar = screen.getAllByRole("link")[index];
  if (bar === undefined) {
    throw new Error(`no timeline bar at ${index}`);
  }
  return bar;
}

function trackOf(container: HTMLElement): HTMLElement {
  const track = container.querySelector<HTMLElement>(".timeline-scrub__track");
  if (track === null) {
    throw new Error("timeline track not rendered");
  }
  return track;
}

function renderTimeline(clips: TimelineClip[] = CLIPS) {
  return render(
    <TimelineScrub durationSeconds={1200} clips={clips} playerSelector="#player" />,
  );
}

describe("TimelineScrub", () => {
  it("positions each clip at its own place in the recording", () => {
    // The reported bug: every bar sat at the left of the track regardless of
    // when its clip occurred.
    renderTimeline();
    expect(screen.getAllByRole("link")).toHaveLength(3);
    expect(barAt(0).style.left).toBe("0%");
    expect(barAt(1).style.left).toBe("25%");
    expect(barAt(2).style.left).toBe("75%");
  });

  it("sizes each clip by how long it runs", () => {
    renderTimeline();
    expect(barAt(0).style.width).toBe("2.5%");
    expect(barAt(2).style.width).toBe("5%");
  });

  it("keeps a very short clip wide enough to click", () => {
    renderTimeline([
      { id: 1, startSeconds: 10, endSeconds: 11, highlightScore: 50, label: "blink" },
    ]);

    expect(Number.parseFloat(barAt(0).style.width)).toBeGreaterThanOrEqual(0.6);
  });

  it("links each bar to its clip", () => {
    renderTimeline();
    const bars = screen.getAllByRole("link");

    expect(bars.map((bar) => bar.getAttribute("href"))).toEqual([
      "#clip-7",
      "#clip-8",
      "#clip-9",
    ]);
  });

  it("orders bars by time even when the clips arrive unsorted", () => {
    renderTimeline([BEST_CLIP, QUIET_CLIP, MIDDLING_CLIP]);

    expect(
      screen.getAllByRole("link").map((bar) => bar.getAttribute("href")),
    ).toEqual(["#clip-7", "#clip-8", "#clip-9"]);
  });

  it("uses only class names some stylesheet defines", () => {
    // Guards the actual defect: Tailwind utilities in a project without
    // Tailwind, which produced no rules at all.
    const { container } = renderTimeline();

    expect(undefinedClassNames(container)).toEqual([]);
  });

  it("grades a bar by its highlight score", () => {
    renderTimeline();
    expect(barAt(0).className).toContain("timeline-scrub__clip--low");
    expect(barAt(1).className).toContain("timeline-scrub__clip--mid");
    expect(barAt(2).className).toContain("timeline-scrub__clip--high");
  });

  it("seeks the player to a clip's start when its bar is clicked", () => {
    const player = document.createElement("video");
    player.id = "player";
    Object.defineProperty(player, "duration", { value: 1200, configurable: true });
    document.body.append(player);

    renderTimeline();
    fireEvent.click(barAt(1));

    expect(player.currentTime).toBe(300);
    player.remove();
  });

  it("does not also scrub the track when a bar is clicked", () => {
    // The track scrubs on click. Without stopPropagation, selecting a clip was
    // immediately overridden by a scrub to wherever the pointer landed.
    const player = document.createElement("video");
    player.id = "player";
    Object.defineProperty(player, "duration", { value: 1200, configurable: true });
    document.body.append(player);

    const { container } = renderTimeline();
    const track = trackOf(container);
    // jsdom reports a zero-width rect, which makes the scrub a no-op and hides
    // the behaviour under test.
    track.getBoundingClientRect = () =>
      ({ left: 0, width: 1000, right: 1000, top: 0, bottom: 0, height: 0, x: 0, y: 0 }) as DOMRect;

    // Halfway across a 1200s timeline is 600s — clearly not clip 9's start.
    fireEvent.click(barAt(2), { clientX: 500 });

    expect(player.currentTime).toBe(900);
    player.remove();
  });

  it("scrubs to the pointer when the track itself is clicked", () => {
    const player = document.createElement("video");
    player.id = "player";
    Object.defineProperty(player, "duration", { value: 1200, configurable: true });
    document.body.append(player);

    const { container } = renderTimeline();
    const track = trackOf(container);
    track.getBoundingClientRect = () =>
      ({ left: 0, width: 1000, right: 1000, top: 0, bottom: 0, height: 0, x: 0, y: 0 }) as DOMRect;

    fireEvent.click(track, { clientX: 500 });

    expect(player.currentTime).toBe(600);
    player.remove();
  });

  it("says so when a recording has no clips yet", () => {
    renderTimeline([]);

    expect(screen.getByText(/no extracted clips/i)).toBeTruthy();
    expect(screen.queryAllByRole("link")).toHaveLength(0);
  });
});
