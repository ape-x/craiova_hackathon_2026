import type { PostflightConnection } from './types';
import { sourceInstant } from './sourceInstant';

export function postflightConnectionEvidence(value: unknown): PostflightConnection | null {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null;
  const data = value as Record<string, unknown>,
    arrival = sourceInstant(data.source_arrival_at),
    start = sourceInstant(data.pickup_window_start),
    end = sourceInstant(data.pickup_window_end),
    pickup = sourceInstant(data.derived_pickup_at);
  if (
    data.source !== 'operator_declared_processing_window' ||
    data.arrival_basis !== 'supplier_schedule_not_observed' ||
    data.pickup_rule !== 'max_window_start_ceil_arrival_plus_processing' ||
    typeof data.incoming_resource_id !== 'string' ||
    !data.incoming_resource_id.trim() ||
    data.incoming_resource_id.length > 128 ||
    typeof data.incoming_resource_fingerprint !== 'string' ||
    !/^[a-f0-9]{64}$/.test(data.incoming_resource_fingerprint) ||
    typeof data.airport_processing_minutes !== 'number' ||
    !Number.isInteger(data.airport_processing_minutes) ||
    data.airport_processing_minutes < 1 ||
    data.airport_processing_minutes > 720 ||
    arrival === null ||
    start === null ||
    end === null ||
    pickup === null ||
    start % 60_000 !== 0 ||
    end % 60_000 !== 0 ||
    pickup % 60_000 !== 0 ||
    end < start ||
    pickup > end ||
    pickup !==
      Math.max(
        start,
        Math.ceil((arrival + data.airport_processing_minutes * 60_000) / 60_000) * 60_000,
      )
  )
    return null;
  return value as PostflightConnection;
}
