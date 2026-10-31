import type { Resource, World } from './types';
import { privateTransfer } from './priceEvidence';
import {
  freshnessTime,
  recordedBooking,
  resourceFreshness,
  sourceInstant,
} from './resourceFreshness';

function cleanLabel(value: unknown): string | null {
  if (typeof value !== 'string') return null;
  return (
    value
      .replace(/[\u0000-\u001f\u007f]/g, ' ')
      .replace(/\s+/g, ' ')
      .trim()
      .slice(0, 160) || null
  );
}

export function resourceTitle(resource: Resource): string {
  if (!privateTransfer(resource)) return resource.id;
  const name = cleanLabel(resource.name);
  return name && name !== resource.id ? name : 'APL private transfer';
}

export function locationName(world: Pick<World, 'airports'>, id: string): string {
  const node = world.airports.find((item) => item.id === id);
  return node && node.kind !== undefined && node.kind !== 'airport'
    ? (cleanLabel(node.name) ?? 'Location reference')
    : (cleanLabel(node?.code) ?? id);
}

export function transferReview(world: World, resource: Resource, now: number) {
  const freshness = resourceFreshness(resource, now);
  const route = resource.provenance?.google_route as Record<string, unknown> | undefined;
  const received = route?.source === 'Google Routes' ? sourceInstant(route.fetched_at) : null;
  return {
    title: resourceTitle(resource),
    status: `${recordedBooking(world, resource) ? 'Booking recorded' : 'Unbooked'} · ${freshness.label}${!resource.available ? ' · Unavailable' : resource.status === 'superseded' ? ' · Superseded' : ''}`,
    sourceTime:
      received === null
        ? 'Road prediction source time unavailable'
        : `Road prediction received ${freshnessTime(received)}`,
  };
}

/** Pass the original source world through; individual map layers enforce live provenance. */
export function geographicContext(world: World, selectedId: string | null) {
  const resource = world.resources.find((item) => item.id === selectedId);
  const selectionId =
    world.mode === 'live' && resource?.kind === 'hotel' ? resource.origin : selectedId;
  const home =
    world.airports.find((item) => item.id === selectionId) ??
    (world.mode === 'live' && resource
      ? world.airports.find((item) => item.id === resource.origin)
      : undefined) ??
    world.airports[0];
  const transfer = resource && privateTransfer(resource);
  const locationLabel = transfer
    ? `${resourceTitle(resource)} · private transfer`
    : home?.kind === 'meeting_point'
      ? `${locationName(world, home.id)} · reported meeting point`
      : home?.kind === 'hotel'
        ? `${locationName(world, home.id)} · accommodation`
        : home
          ? `${home.code} recovery operations`
          : world.mode === 'live'
            ? 'Live recovery operations'
            : 'Simulation workspace';
  const siteLabel = transfer
    ? 'TRANSFER'
    : home?.kind === 'meeting_point'
      ? 'MEETING POINT'
      : home?.kind === 'hotel'
        ? 'HOTEL'
        : (home?.code ?? 'NETWORK');
  return { world, selectionId, home, locationLabel, siteLabel };
}
