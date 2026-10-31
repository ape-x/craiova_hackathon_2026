import type { Observation } from './types';

export function observationInfo(item: Observation, now = Date.now()) {
  const timestamp = item.observed_at ?? item.timestamp;
  const milliseconds = timestamp ? Date.parse(timestamp) : NaN;
  const ageSeconds = (now - milliseconds) / 1000;
  const knownTime = Number.isFinite(ageSeconds) && ageSeconds >= -60;
  const sample = item.mode === 'SANDBOX' || item.mode === 'REPLAY';
  const fresh = knownTime && ageSeconds <= 90 && !sample;
  const label =
    item.mode === 'SANDBOX'
      ? 'SANDBOX'
      : item.mode === 'REPLAY'
        ? 'REPLAY'
        : !knownTime
          ? 'TIME UNKNOWN'
          : fresh
            ? 'OBSERVED'
            : 'STALE';
  return {
    timestamp,
    milliseconds,
    knownTime,
    ageSeconds: knownTime ? Math.max(0, Math.floor(ageSeconds)) : null,
    fresh,
    label,
    source: String(item.source ?? 'External feed'),
  };
}
