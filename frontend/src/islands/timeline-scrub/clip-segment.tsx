import { cva, type VariantProps } from "class-variance-authority";

import { cn } from "@/utils/cn";

/**
 * Positioning and colour live in timeline-scrub.css, not in utility classes.
 * These were Tailwind utilities, but the project has no Tailwind — so
 * `absolute` produced no rule, the bars stayed statically positioned, and the
 * inline `left` offset was inert. Every clip stacked at the left of the track.
 */
const clipSegmentVariants = cva("timeline-scrub__clip", {
  variants: {
    tone: {
      low: "timeline-scrub__clip--low",
      mid: "timeline-scrub__clip--mid",
      high: "timeline-scrub__clip--high",
    },
    active: {
      true: "timeline-scrub__clip--active",
      false: "",
    },
  },
  defaultVariants: {
    tone: "mid",
    active: false,
  },
});

export type ClipSegmentTone = NonNullable<
  VariantProps<typeof clipSegmentVariants>["tone"]
>;

export function scoreToTone(score: number): ClipSegmentTone {
  if (score >= 70) {
    return "high";
  }
  if (score >= 40) {
    return "mid";
  }
  return "low";
}

type ClipSegmentProps = {
  label: string;
  leftPercent: number;
  widthPercent: number;
  tone: ClipSegmentTone;
  active: boolean;
  onSelect: () => void;
};

/**
 * A clip's span on the scrub bar. Selects and seeks only — it used to also
 * be a same-page `<a href="#clip-N">`, which meant clicking a bar to scrub
 * the player also yanked the viewport down to that clip's card. The jump
 * link now lives in ClipLink, rendered in a separate strip below the track.
 */
export function ClipSegment({
  label,
  leftPercent,
  widthPercent,
  tone,
  active,
  onSelect,
}: ClipSegmentProps) {
  return (
    <button
      type="button"
      className={cn(clipSegmentVariants({ tone, active }))}
      style={{
        left: `${leftPercent}%`,
        // Keep a very short clip clickable rather than sub-pixel wide.
        width: `${Math.max(widthPercent, 0.6)}%`,
      }}
      title={label}
      aria-label={label}
      onClick={(event) => {
        // The track scrubs on click; a bar should select its clip instead.
        event.stopPropagation();
        onSelect();
      }}
    />
  );
}
