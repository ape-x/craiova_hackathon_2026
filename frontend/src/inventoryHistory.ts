import { api } from './api';
import type { InventoryResourceRecord } from './types';

const archiveReasons: Record<string, string> = {
  capacity_pressure:
    'An older inactive service was moved to history to make room for current inventory.',
  superseded_quote: 'A newer supplier quote replaced this quote.',
  hotel_quotes_replaced: 'A fresh hotel search replaced the earlier room quotes.',
  removed_from_active:
    'The service was removed from active inventory; its source details were retained.',
  resource_revision: 'An earlier version of the service was retained for reference.',
  location_revision: 'An earlier version of the location was retained for reference.',
};

export function archiveReasonLabel(reason: string): string {
  return Object.hasOwn(archiveReasons, reason)
    ? archiveReasons[reason]
    : 'Retained for historical reference; further reason details are unavailable.';
}

function object(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}

/** A historic reference must never silently resolve to another operation or service. */
export function verifyInventoryRecord(
  value: unknown,
  scenarioId: string,
  resourceId: string,
): InventoryResourceRecord {
  if (
    !object(value) ||
    value.scenario_id !== scenarioId ||
    !object(value.resource) ||
    value.resource.id !== resourceId ||
    !['flight', 'hotel', 'bus'].includes(String(value.resource.kind)) ||
    !['name', 'source', 'origin', 'destination'].every(
      (key) => typeof (value.resource as Record<string, unknown>)[key] === 'string',
    ) ||
    !['departure', 'arrival'].every((key) =>
      Number.isFinite((value.resource as Record<string, unknown>)[key]),
    ) ||
    (value.resource.provenance != null && !object(value.resource.provenance)) ||
    typeof value.archived !== 'boolean' ||
    !Array.isArray(value.locations) ||
    !value.locations.every(
      (node) =>
        object(node) &&
        typeof node.id === 'string' &&
        typeof node.name === 'string' &&
        Number.isFinite(node.lat) &&
        Number.isFinite(node.lon),
    ) ||
    (value.archived_at !== null && typeof value.archived_at !== 'string') ||
    (value.archived_version !== null && !Number.isInteger(value.archived_version)) ||
    (value.archive_reason !== null && typeof value.archive_reason !== 'string')
  ) {
    throw new Error(
      'The saved source record could not be verified for this operation and service.',
    );
  }
  return value as unknown as InventoryResourceRecord;
}

export async function loadInventoryRecord(
  scenarioId: string,
  resourceId: string,
  signal: AbortSignal,
): Promise<InventoryResourceRecord> {
  signal.throwIfAborted();
  const result = await api.inventoryResource(resourceId, scenarioId, signal);
  // Some transports may finish after cancellation. Do not show a closed/old request.
  signal.throwIfAborted();
  return verifyInventoryRecord(result, scenarioId, resourceId);
}
