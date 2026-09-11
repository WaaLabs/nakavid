import { cva } from "class-variance-authority";

import type { ClipSegmentTone } from "./clip-segment";
import { cn } from "@/utils/cn";

const clipLinkVariants = cva("timeline-scrub__link", {
  variants: {
    tone: {
      low: "timeline-scrub__link--low",
      mid: "timeline-scrub__link--mid",
      high: "timeline-scrub__link--high",
    },
  },
  defaultVariants: {
    tone: "mid",
  },
});

type ClipLinkProps = {
  href: string;
  label: string;
  number: number;
  tone: ClipSegmentTone;
};

/**
 * A jump link to a clip's card further down the page. Deliberately not the
 * same element as its bar on the scrub track above: a clip's span can be a
 * few seconds on a 90-minute recording, far too thin a target to also carry
 * same-page navigation. See ClipSegment for the bug this split fixes.
 */
export function ClipLink({ href, label, number, tone }: ClipLinkProps) {
  return (
    <a href={href} className={cn(clipLinkVariants({ tone }))} title={label}>
      Clip {number}
    </a>
  );
}
