export type CombineClip = {
  id: number;
  label: string;
  streamUrl: string;
  durationSeconds: number;
  highlightScore: number;
};

export type DragCombineProps = {
  clips: CombineClip[];
  submitUrl: string;
  csrfToken: string;
};

export type SubmitResponse = {
  combineId: number;
  jobId: number;
  queueStatusUrl: string;
};
