export type TimelineClip = {
  id: number;
  startSeconds: number;
  endSeconds: number;
  highlightScore: number;
  label: string;
};

export type TimelineProps = {
  durationSeconds: number;
  clips: TimelineClip[];
  playerSelector: string;
};
