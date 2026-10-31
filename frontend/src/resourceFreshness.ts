import { useEffect, useState } from 'react';
import type { Resource, World } from './types';

export interface ResourceFreshness {
  state: 'current' | 'stale' | 'unknown';
  label: string;
  detail: string;
  basis: 'valid_until' | 'quoted_at' | 'app_receipt' | null;
  quotedAt: number | null;
  receivedAt: number | null;
  routeReceivedAt: number | null;
  cutoff: number | null;
}

export function sourceInstant(value: unknown): number | null {
  if (typeof value !== 'string') return null;
  const match =
    /^(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2})(?::(\d{2})(\.\d{1,6})?)?(Z|[+-]\d{2}:?\d{2})$/.exec(
      value,
    );
  if (!match) return null;
  const calendar = `${match[1]}T${match[2]}:${match[3] ?? '00'}`;
  const wall = Date.parse(`${calendar}${match[4] ?? ''}Z`);
  const instant = Date.parse(value);
  return Number.isFinite(wall) &&
    Number.isFinite(instant) &&
    new Date(wall).toISOString().slice(0, 19) === calendar
    ? instant
    : null;
}

// Presentation mirrors live-resource and return-route receipt checks; eligibility remains server-side.
export function resourceFreshness(resource: Resource, now: number): ResourceFreshness {
  const quotedAt = sourceInstant(resource.provenance?.quoted_at);
  const apl = resource.source === 'Airport Pickups London';
  const receipt = apl ? resource.quote_freshness : null;
  const receivedAt = sourceInstant(receipt?.received_at);
  const returning = apl && resource.airport_arrival_assumption != null;
  const rawRoute = returning ? resource.provenance?.google_route : null;
  const route =
    rawRoute !== null && typeof rawRoute === 'object' && !Array.isArray(rawRoute)
      ? (rawRoute as Record<string, unknown>)
      : null;
  const routeReceivedAt =
    route?.source === 'Google Routes' ? sourceInstant(route.fetched_at) : null;
  const unknown: ResourceFreshness = {
    state: 'unknown',
    label: 'Freshness unknown',
    basis: null,
    quotedAt,
    receivedAt,
    routeReceivedAt,
    cutoff: null,
    detail: 'No usable validity or quote time. Revalidation is required for a new plan.',
  };
  if (resource.source === 'synthetic')
    return {
      ...unknown,
      label: 'Source unverified',
      detail: 'Synthetic inventory is not accepted in live plans.',
    };
  if (!Number.isFinite(now)) return unknown;
  if (apl && (!receipt || resource.valid_until != null || resource.provenance?.quoted_at != null))
    return {
      ...unknown,
      detail:
        'APL requires an app receipt window without an invented supplier quote time or expiry. Revalidation is required.',
    };
  if (resource.valid_until) {
    const cutoff = sourceInstant(resource.valid_until);
    if (cutoff === null)
      return {
        ...unknown,
        detail: 'The recorded validity time is invalid. Revalidation is required.',
      };
    const current = cutoff > now;
    return {
      state: current ? 'current' : 'stale',
      label: current ? 'Within validity' : 'Validity expired',
      basis: 'valid_until',
      quotedAt,
      receivedAt,
      routeReceivedAt,
      cutoff,
      detail: current
        ? 'Within the recorded validity window; this does not guarantee supplier stock.'
        : 'Recorded validity has ended. Revalidation is required for a new plan.',
    };
  }
  if (receipt != null) {
    if (receipt.basis !== 'app_receipt' || receipt.max_age_seconds !== 120 || receivedAt === null)
      return {
        ...unknown,
        detail:
          'The app receipt time or local freshness policy is unavailable. Revalidation is required.',
      };
    if (receivedAt > now)
      return {
        ...unknown,
        detail: 'The app receipt time is in the future. Freshness cannot be established.',
      };
    if (returning && routeReceivedAt === null)
      return {
        ...unknown,
        detail:
          'The Google Routes receipt time for this return is missing or invalid. Both APL and road prediction receipts are required for a new plan.',
      };
    if (returning && routeReceivedAt! > now)
      return {
        ...unknown,
        detail:
          'The Google Routes receipt time is in the future. Return planning freshness cannot be established.',
      };
    const cutoff = returning
      ? Math.min(receivedAt + 120_000, routeReceivedAt! + 120_000)
      : receivedAt + 120_000;
    return {
      state: now <= cutoff ? 'current' : 'stale',
      label: now <= cutoff ? 'Within freshness window' : 'Quote stale',
      basis: 'app_receipt',
      quotedAt,
      receivedAt,
      routeReceivedAt,
      cutoff,
      detail: returning
        ? 'The app applies a 120-second window to both the APL response and Google Routes prediction receipts. The earlier cutoff limits return planning; revalidate both sources for a new plan. Supplier quote time and expiry are unknown; this is not a supplier hold.'
        : 'The app applies a 120-second planning freshness window from receipt. Supplier quote time and expiry are unknown; this is not a supplier hold.',
    };
  }
  if (!resource.requires_revalidation || quotedAt === null) return unknown;
  if (quotedAt > now)
    return {
      ...unknown,
      detail: 'The source quote time is in the future. Freshness cannot be established.',
    };
  const cutoff = quotedAt + 120_000;
  const current = now <= cutoff;
  return {
    state: current ? 'current' : 'stale',
    label: current ? 'Within freshness window' : 'Quote stale',
    basis: 'quoted_at',
    quotedAt,
    receivedAt,
    routeReceivedAt,
    cutoff,
    detail:
      'This quote has a 120-second local freshness window from the source quote time. This is not a supplier expiry or a hold.',
  };
}

export function recordedBooking(world: Pick<World, 'offers'>, resource: Resource): boolean {
  return (
    resource.booking_status === 'confirmed' ||
    world.offers.some(
      (offer) =>
        offer.status === 'confirmed' &&
        offer.itinerary.legs.some((leg) => leg.resource_id === resource.id),
    )
  );
}

export function liveResourceStatus(
  resource: Resource,
  freshness: ResourceFreshness,
  booked: boolean,
) {
  const planningCurrent =
    resource.available && resource.status !== 'superseded' && freshness.state === 'current';
  if (resource.status === 'superseded')
    return { label: 'Superseded', tone: 'red', planningCurrent: false } as const;
  if (!resource.available)
    return { label: 'Unavailable', tone: 'red', planningCurrent: false } as const;
  if (booked) return { label: 'Booking recorded', tone: 'mint', planningCurrent } as const;
  if (!planningCurrent)
    return { label: freshness.label, tone: 'amber', planningCurrent: false } as const;
  return {
    label:
      resource.capacity_scope === 'private_vehicle_quote'
        ? 'Unbooked vehicle quote'
        : resource.capacity_scope === 'quoted_request'
          ? 'Unbooked quote'
          : 'Documented service',
    tone: 'cyan',
    planningCurrent,
  } as const;
}

const utcClock = new Intl.DateTimeFormat('en-GB', {
  timeZone: 'UTC',
  year: 'numeric',
  month: 'short',
  day: '2-digit',
  hour: '2-digit',
  minute: '2-digit',
  second: '2-digit',
  hourCycle: 'h23',
});
export const freshnessTime = (instant: number) => `${utcClock.format(instant)} UTC`;

export function startFreshnessClock(onTick: (now: number) => void): () => void {
  onTick(Date.now());
  const timer = setInterval(() => onTick(Date.now()), 1000);
  return () => clearInterval(timer);
}

export function useFreshnessClock(enabled: boolean): number {
  const [now, setNow] = useState(Date.now);
  useEffect(() => (enabled ? startFreshnessClock(setNow) : undefined), [enabled]);
  return now;
}
