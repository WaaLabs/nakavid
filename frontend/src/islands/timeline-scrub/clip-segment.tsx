import { cva, type VariantProps } from "class-variance-authority";

import { cn } from "@/utils/cn";

const clipSegmentVariants = cva(
  "absolute top-1 bottom-1 rounded-sm border cursor-pointer transition-opacity focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-sky-300",
  {
    variants: {
      tone: {
        low: "bg-slate-500/80 border-slate-300/40 hover:opacity-90",
        mid: "bg-amber-500/85 border-amber-200/50 hover:opacity-95",
        high: "bg-emerald-500/90 border-emerald-100/60 hover:opacity-100",
      },
      active: {
        true: "ring-2 ring-sky-300 z-10",
        false: "",
      },
    },
    defaultVariants: {
      tone: "mid",
      active: false,
    },
  },
);

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
        width: `${Math.max(widthPercent, 0.4)}%`,
      }}
      title={label}
      aria-label={label}
      onClick={onSelect}
    />
  );
}
