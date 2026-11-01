import { useEffect, useState } from 'react';
import type { Resource, World } from './types';
import { sourceInstant } from './sourceInstant';
export { sourceInstant } from './sourceInstant';
import { postflightConnectionEvidence } from './postflightConnection';

export interface ResourceFreshness {
  state: 'current' | 'stale' | 'unknown';
  label: string;
  detail: string;
  basis: 'valid_until' | 'quoted_at' | 'app_receipt' | null;
  quotedAt: number | null;
  receivedAt: number | null;
  routeReceivedAt: number | null;
  cutoff: number | null;
  incomingResourceId?: string;
  incomingQuotedAt?: number;
  incomingValidUntil?: number | null;
}

function object(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

// Cross-check public timing/source fields. The server verifies the immutable fingerprint
// and complete planning eligibility; this presentation check cannot replace that validation.
function incomingConnectionTiming(resource: Resource, now: number, world?: World) {
  const proof = postflightConnectionEvidence(resource.provenance?.postflight_connection);
  const epoch = sourceInstant(world?.epoch);
  if (
    !proof ||
    !world ||
    world.mode !== 'live' ||
    epoch === null ||
    resource.provenance?.direction !== 'after_flight_airport_to_airport'
  )
    return null;
  const matches = world.resources.filter((item) => item.id === proof.incoming_resource_id);
  if (matches.length !== 1) return null;
  const incoming = matches[0],
    quote = incoming.provenance;
  const departure = sourceInstant(quote?.departure_at),
    arrival = sourceInstant(quote?.arrival_at);
  const quotedAt = sourceInstant(quote?.quoted_at);
  const validUntil = sourceInstant(incoming.valid_until),
    sourceExpiry = sourceInstant(quote?.expires_at);
  const cab = object(resource.provenance?.airport_cab_quote),
    query = object(cab?.request);
  const pickup = sourceInstant(proof.derived_pickup_at);
  const groupId = resource.eligible_group_ids?.[0];
  if (
    incoming.kind !== 'flight' ||
    !incoming.available ||
    incoming.status !== 'quoted' ||
    !incoming.source ||
    incoming.source === 'synthetic' ||
    incoming.source !== quote?.provider ||
    quote?.mode !== 'live' ||
    quote?.currency !== 'EUR' ||
    incoming.capacity_scope !== 'quoted_request' ||
    incoming.booking_status !== 'unbooked' ||
    !incoming.requires_revalidation ||
    !groupId ||
    resource.eligible_group_ids?.length !== 1 ||
    incoming.eligible_group_ids?.length !== 1 ||
    incoming.eligible_group_ids[0] !== groupId ||
    !Number.isInteger(incoming.capacity) ||
    incoming.capacity < 1 ||
    incoming.capacity > 7 ||
    incoming.capacity !== resource.capacity ||
    quote?.passengers !== incoming.capacity ||
    incoming.origin === 'OTP' ||
    resource.origin !== 'OTP' ||
    resource.destination !== 'CRA' ||
    incoming.destination !== resource.origin ||
    quote?.origin !== incoming.origin ||
    quote?.destination !== incoming.destination ||
    !incoming.external_offer_id ||
    incoming.external_offer_id !== quote?.provider_offer_id ||
    departure === null ||
    arrival === null ||
    arrival <= departure ||
    arrival !== sourceInstant(proof.source_arrival_at) ||
    incoming.departure !== Math.floor((departure - epoch) / 60_000) ||
    incoming.arrival !== Math.ceil((arrival - epoch) / 60_000) ||
    pickup === null ||
    pickup !== sourceInstant(query?.pickup_at) ||
    resource.departure !== Math.ceil((pickup - epoch) / 60_000) ||
    quotedAt === null ||
    quotedAt > now ||
    (incoming.valid_until != null && validUntil === null) ||
    (quote?.expires_at != null && sourceExpiry === null) ||
    validUntil !== sourceExpiry
  )
    return null;
  // A newer current-world observation for the same supplier offer supersedes this binding,
  // even if a delayed world update has not marked its old record unavailable yet.
  if (
    world.resources.some(
      (item) =>
        item.id !== incoming.id &&
        item.kind === 'flight' &&
        item.source === incoming.source &&
        item.external_offer_id === incoming.external_offer_id &&
        item.eligible_group_ids?.length === 1 &&
        item.eligible_group_ids[0] === groupId &&
        (sourceInstant(item.provenance?.quoted_at) ?? -Infinity) > quotedAt,
    )
  )
    return null;
  return {
    resourceId: incoming.id,
    quotedAt,
    validUntil,
    cutoff: Math.min(quotedAt + 120_000, validUntil ?? Infinity),
  };
}

// Presentation mirrors live-resource and return-route receipt checks; eligibility remains server-side.
export function resourceFreshness(
  resource: Resource,
  now: number,
  world?: World,
): ResourceFreshness {
  const quotedAt = sourceInstant(resource.provenance?.quoted_at);
  const apl = resource.source === 'Airport Pickups London';
  const cab = resource.source === 'Bucharest.CAB';
  const postflight =
    cab &&
    (resource.provenance?.direction === 'after_flight_airport_to_airport' ||
      Object.hasOwn(resource.provenance ?? {}, 'postflight_connection'));
  const receiptSource = apl || cab;
  const receipt = receiptSource ? resource.quote_freshness : null;
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
  if (
    receiptSource &&
    (!receipt || resource.valid_until != null || resource.provenance?.quoted_at != null)
  )
    return {
      ...unknown,
      detail: `${apl ? 'APL' : 'Bucharest.CAB'} requires an app receipt window without an invented supplier quote time or expiry. Revalidation is required.`,
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
    let advanceCutoff: number | null = null;
    if (cab) {
      const quote = object(resource.provenance?.airport_cab_quote);
      const query = object(quote?.request),
        policy = object(quote?.policy);
      const pickup = sourceInstant(query?.pickup_at);
      const required = policy?.required_advance_hours,
        day = policy?.min_advance_day_hours,
        night = policy?.min_advance_night_hours;
      const hours = (value: unknown): value is number =>
        typeof value === 'number' && Number.isInteger(value) && value >= 0 && value <= 168;
      if (
        pickup === null ||
        !hours(required) ||
        !hours(day) ||
        !hours(night) ||
        required !== Math.max(day, night) ||
        policy?.advance_policy_basis !== 'conservative_maximum_of_source_day_night_rules'
      )
        return {
          ...unknown,
          detail:
            'The source pickup or advance-notice policy is unavailable. Planning eligibility cannot be established.',
        };
      advanceCutoff = pickup - required * 3_600_000;
    }
    const incoming = postflight ? incomingConnectionTiming(resource, now, world) : null;
    if (postflight && !incoming)
      return {
        ...unknown,
        label: 'Connection freshness unknown',
        detail:
          'The current bound incoming flight is missing, unavailable or inconsistent with this transfer. Revalidate the connection before a new plan.',
      };
    const cutoff = returning
      ? Math.min(receivedAt + 120_000, routeReceivedAt! + 120_000)
      : Math.min(receivedAt + 120_000, advanceCutoff ?? Infinity, incoming?.cutoff ?? Infinity);
    const current = now <= cutoff && (incoming?.validUntil == null || now < incoming.validUntil);
    return {
      state: current ? 'current' : 'stale',
      label: postflight
        ? current
          ? 'Within connection freshness'
          : 'Connection quote stale'
        : current
          ? 'Within freshness window'
          : advanceCutoff !== null && now > advanceCutoff
            ? 'Advance-notice window ended'
            : 'Quote stale',
      basis: 'app_receipt',
      quotedAt,
      receivedAt,
      routeReceivedAt,
      cutoff,
      ...(incoming
        ? {
            incomingResourceId: incoming.resourceId,
            incomingQuotedAt: incoming.quotedAt,
            incomingValidUntil: incoming.validUntil,
          }
        : {}),
      detail: postflight
        ? 'Connection planning uses the earliest of the CAB receipt window (120 seconds), source advance-notice limit, incoming flight’s 120-second quote window and any incoming supplier validity deadline. CAB supplier expiry and fleet availability remain unknown; this is not a vehicle hold. Source revisions and eligibility still require backend revalidation.'
        : cab
          ? 'The application limits planning to the earlier of 120 seconds after receipt and the supplier’s conservative advance-notice cutoff. Supplier quote time and expiry are unknown; this is not a vehicle hold.'
          : returning
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
