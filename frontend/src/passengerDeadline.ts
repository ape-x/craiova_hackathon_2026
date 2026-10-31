import { utcInputToIso } from './liveData';

const MINUTE_MS = 60_000;
export const LIVE_PASSENGER_HORIZON_MINUTES = 7 * 24 * 60;

function epochMilliseconds(epoch: string): number {
  const instant = Date.parse(epoch);
  if (!/(?:Z|[+-]\d{2}:\d{2})$/i.test(epoch) || !Number.isFinite(instant))
    throw new Error('The operation time is unavailable. Refresh your journey before editing.');
  return instant;
}

/** UTC form text from the exact stored instant, without advancing old deadlines. */
export function passengerDeadlineUtcInput(epoch: string, minute: number): string {
  const instant = epochMilliseconds(epoch) + minute * MINUTE_MS;
  if (!Number.isSafeInteger(minute) || minute < 0 || instant % MINUTE_MS !== 0)
    throw new Error('The deadline must be an exact operation minute.');
  const date = new Date(instant);
  if (!Number.isFinite(date.getTime())) throw new Error('The deadline is outside the date range.');
  return date.toISOString().slice(0, 16);
}

/** datetime-local is explicitly labelled UTC; never interpret it in the browser zone. */
export function parsePassengerDeadlineUtc(
  value: string,
  epoch: string,
  currentMinute: number,
): number {
  if (!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$/.test(value))
    throw new Error('Enter a complete UTC arrival date and time, to the minute.');
  const instant = Date.parse(utcInputToIso(value));
  const deadline = (instant - epochMilliseconds(epoch)) / MINUTE_MS;
  if (!Number.isSafeInteger(deadline) || deadline < 0)
    throw new Error('The deadline must be an exact operation minute.');
  if (!Number.isSafeInteger(currentMinute) || currentMinute < 0)
    throw new Error('The current operation time is unavailable. Refresh your journey.');
  if (deadline < currentMinute)
    throw new Error('Choose an arrival deadline at or after the current server time.');
  if (deadline > currentMinute + LIVE_PASSENGER_HORIZON_MINUTES)
    throw new Error('Choose an arrival deadline within seven days of the current server time.');
  return deadline;
}
