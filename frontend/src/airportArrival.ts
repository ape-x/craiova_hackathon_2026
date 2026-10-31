import type { AirportArrivalAssumption } from './types';
import { sourceInstant } from './resourceFreshness';

function object(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}
export function airportArrivalEvidence(value: unknown): AirportArrivalAssumption | null {
  const data = object(value),
    dropoff = object(data?.dropoff),
    point = object(dropoff?.coordinates);
  if (
    !data ||
    data.source !== 'operator_declared' ||
    data.confirmed !== true ||
    data.departure_terminal !== null ||
    typeof data.airport_id !== 'string' ||
    !/^[A-Z]{3}$/.test(data.airport_id) ||
    ![
      'group_id',
      'group_fingerprint',
      'scenario_id',
      'onward_resource_id',
      'onward_flight_fingerprint',
    ].every((key) => typeof data[key] === 'string' && Boolean(data[key])) ||
    sourceInstant(data.epoch) === null ||
    sourceInstant(data.onward_departure_at) === null ||
    typeof data.processing_minutes !== 'number' ||
    !Number.isInteger(data.processing_minutes) ||
    data.processing_minutes < 1 ||
    data.processing_minutes > 720 ||
    typeof dropoff?.label !== 'string' ||
    !dropoff.label.trim() ||
    dropoff.label.length > 500 ||
    typeof point?.latitude !== 'number' ||
    typeof point.longitude !== 'number' ||
    !Number.isFinite(point.latitude + point.longitude) ||
    Math.abs(point.latitude) > 90 ||
    Math.abs(point.longitude) > 180
  )
    return null;
  return value as AirportArrivalAssumption;
}

export function supplierMeetingEvidence(
  value: unknown,
): { name: string | null; instructions: string | null } | null {
  const data = object(value);
  if (!data) return null;
  const name =
    typeof data.name === 'string' &&
    data.name.trim() &&
    data.name.length <= 500 &&
    !/[\u0000-\u001f\u007f]/.test(data.name)
      ? data.name.trim()
      : null;
  const instructions =
    typeof data.instructions === 'string' &&
    data.instructions.trim() &&
    data.instructions.length <= 4096 &&
    !/[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f]/.test(data.instructions)
      ? data.instructions.trim()
      : null;
  return name || instructions ? { name, instructions } : null;
}
export function resourceMeetingEvidence(provenance?: Record<string, unknown>) {
  return supplierMeetingEvidence(object(provenance?.apl_quote)?.meeting_point);
}
