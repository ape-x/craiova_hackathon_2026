import type { Resource } from './types';

export const TIMELINE_TICK_HEIGHT = 24;
export const TIMELINE_BAR_HEIGHT = 18;
const ROW_STEP = 20;
const LANE_PADDING = 4;
const EMPTY_LANE_HEIGHT = 26;

export interface TimelineBar<T> {
  resource: T;
  row: number;
  left: number;
  width: number;
  top: number;
}

/** Clip to the visible window, then reuse a row once its previous interval ends. */
export function layoutTimelineLane<T extends Pick<Resource, 'departure' | 'arrival'>>(
  resources: readonly T[],
  start: number,
  span: number,
): { bars: TimelineBar<T>[]; height: number } {
  if (!Number.isFinite(start) || !Number.isFinite(span) || span <= 0) {
    return { bars: [], height: EMPTY_LANE_HEIGHT };
  }

  const visible = resources
    .map((resource, index) => ({
      resource,
      index,
      start: Math.max(start, resource.departure),
      end: Math.min(start + span, resource.arrival),
    }))
    .filter((interval) => interval.end > interval.start)
    .sort((a, b) => a.start - b.start || a.end - b.end || a.index - b.index);
  const rowEnds: number[] = [];
  const bars = visible.map((interval) => {
    let row = rowEnds.findIndex((end) => end <= interval.start);
    if (row === -1) row = rowEnds.length;
    rowEnds[row] = interval.end;
    return {
      resource: interval.resource,
      row,
      left: ((interval.start - start) / span) * 100,
      width: ((interval.end - interval.start) / span) * 100,
      top: LANE_PADDING + row * ROW_STEP,
    };
  });

  return {
    bars,
    height: EMPTY_LANE_HEIGHT + Math.max(0, rowEnds.length - 1) * ROW_STEP,
  };
}
