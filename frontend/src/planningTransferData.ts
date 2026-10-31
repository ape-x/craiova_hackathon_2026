import type { TransferQuoteResult } from './transferQuoteData';
import type { AirportArrivalAssumption, World } from './types';
import { utcInputToIso } from './liveData';
import { freshnessTime, resourceFreshness, sourceInstant } from './resourceFreshness';

export interface PlanningTransferForm {
  direction?: 'initial_airport_to_hotel' | 'hotel_to_airport';
  onward_resource_id?: string;
  airport_processing_minutes?: string;
  group_id: string;
  hotel_node_id: string;
  pickup_at: string;
  suitcases: string;
  label: string;
  latitude: string;
  longitude: string;
  confirmed: boolean;
}
interface PlanningTransferBaseInput {
  group_id: string;
  hotel_node_id: string;
  pickup_at: string;
  suitcases: number;
}
type DeclaredPoint = { label: string; latitude: number; longitude: number; confirmed: true };
export type PlanningTransferInput = PlanningTransferBaseInput &
  (
    | {
        direction?: 'initial_airport_to_hotel';
        initial_point: DeclaredPoint;
        airport_dropoff?: never;
        onward_resource_id?: never;
        airport_processing_minutes?: never;
      }
    | {
        direction: 'hotel_to_airport';
        initial_point?: never;
        airport_dropoff: DeclaredPoint;
        onward_resource_id: string;
        airport_processing_minutes: number;
      }
  );
export interface PlanningBlocker {
  code: string;
  message: string;
}
export interface DatedDrivingRoute {
  source: string;
  source_url: string;
  fetched_at: string;
  request: {
    origin: { latitude: number; longitude: number };
    destination: { latitude: number; longitude: number };
    departure_time: string;
    travel_mode: 'DRIVE';
    routing_preference: 'TRAFFIC_AWARE';
  };
  duration_seconds: string;
  distance_meters: number;
  duration_guaranteed: false;
  encoded_polyline?: string | null;
  road_endpoints: {
    origin: { latitude: number; longitude: number };
    destination: { latitude: number; longitude: number };
  };
  warnings: string[];
}
export interface PlanningTransferQuote {
  direction?: 'initial_airport_to_hotel' | 'hotel_to_airport';
  airport_arrival_assumption?: AirportArrivalAssumption | null;
  onward_flight?: {
    resource_id: string;
    origin: string;
    destination: string;
    departure_at: string;
    departure: number;
    source: string;
    departure_terminal: null;
  } | null;
  quote_id: string;
  state: 'ready' | 'blocked';
  group_id: string;
  hotel_node_id: string;
  source_version: number;
  created_at: string;
  usable_until: string;
  initial_point: {
    id: string;
    label: string;
    latitude: number;
    longitude: number;
    source: 'operator_declared';
    confirmed: true;
    airport_context_id: string;
  } | null;
  hotel: {
    node_id: string;
    hotel_id: string;
    name: string;
    address: string;
    city: string | null;
    country: string | null;
    latitude: number;
    longitude: number;
  } | null;
  transfer: TransferQuoteResult | null;
  route: DatedDrivingRoute | null;
  road_snap_offsets_m?: { origin: number; destination: number } | null;
  blockers: PlanningBlocker[];
  options: { option_id: string; importable: boolean; blockers: PlanningBlocker[] }[];
  replayed: boolean;
}

export function returnFlightChoices(world: World, groupId: string, now: number) {
  const group = world.groups.find((item) => item.id === groupId);
  if (!group) return [];
  return world.resources
    .filter(
      (item) =>
        item.kind === 'flight' &&
        item.source !== 'synthetic' &&
        item.eligible_group_ids?.length === 1 &&
        item.eligible_group_ids[0] === groupId,
    )
    .map((resource) => {
      const freshness = resourceFreshness(resource, now);
      const departure = sourceInstant(resource.provenance?.departure_at),
        epoch = sourceInstant(world.epoch);
      const sourceValid =
        resource.provenance?.mode === 'live' &&
        resource.provenance.provider === resource.source &&
        Boolean(resource.external_offer_id) &&
        resource.provenance.provider_offer_id === resource.external_offer_id &&
        resource.provenance.origin === resource.origin &&
        resource.provenance.destination === resource.destination &&
        departure !== null &&
        epoch !== null &&
        Math.floor((departure - epoch) / 60_000) === resource.departure;
      const reason = !sourceValid
        ? 'Source evidence incomplete'
        : freshness.state !== 'current'
          ? `${freshness.label} — refresh flight quote`
          : !resource.available || resource.status === 'superseded'
            ? 'Unavailable or superseded'
            : resource.booking_status !== 'unbooked'
              ? 'Requires separate booking handling'
              : resource.capacity < group.size || resource.arrival > group.deadline
                ? 'Does not fit group requirements'
                : departure! <= now
                  ? 'Departure has passed'
                  : null;
      return {
        resource,
        eligible: reason === null,
        reason,
        departureLabel: departure === null ? 'Departure unavailable' : freshnessTime(departure),
      };
    });
}

export function planningTransferInput(
  form: PlanningTransferForm,
  world: World,
  now = Date.now(),
): PlanningTransferInput {
  const group = world.groups.find((item) => item.id === form.group_id);
  if (world.mode !== 'live' || !group)
    throw new Error('Choose an existing group in the live operation.');
  if (
    group.status === 'confirmed' ||
    world.offers.some((offer) => offer.group_id === group.id && offer.status === 'confirmed')
  )
    throw new Error(
      'This group has recorded bookings; they cannot be replaced by this quote search.',
    );
  if (!world.airports.some((node) => node.id === form.hotel_node_id && node.kind === 'hotel'))
    throw new Error('Choose an imported supplier hotel property.');
  const label = form.label.trim();
  const returning = form.direction === 'hotel_to_airport';
  if (label.length < 2 || label.length > 160 || /[\u0000-\u001f\u007f]/.test(label))
    throw new Error(
      `Describe the ${returning ? 'airport drop-off' : 'initial meeting point'} using 2–160 characters.`,
    );
  const latitude = Number(form.latitude),
    longitude = Number(form.longitude);
  if (
    !form.latitude.trim() ||
    !form.longitude.trim() ||
    !Number.isFinite(latitude) ||
    !Number.isFinite(longitude) ||
    Math.abs(latitude) > 90 ||
    Math.abs(longitude) > 180
  )
    throw new Error(
      'Enter the actual meeting point latitude (−90 to 90) and longitude (−180 to 180).',
    );
  if (!form.confirmed)
    throw new Error(
      returning
        ? 'Confirm the reported airport drop-off and operator processing allowance.'
        : 'Confirm where this group will be ready at pickup.',
    );
  if (!/^\d+$/.test(form.suitcases) || Number(form.suitcases) > 200)
    throw new Error('Enter an explicit suitcase count from 0 to 200.');
  const pickup_at = utcInputToIso(form.pickup_at);
  const pickup = Date.parse(pickup_at);
  if (pickup <= now || pickup % 60_000 !== 0)
    throw new Error('Choose a future pickup time aligned to a whole minute.');
  const epoch = Date.parse(world.epoch);
  if (
    !Number.isFinite(epoch) ||
    pickup > epoch + group.deadline * 60_000 ||
    (group.ready_at != null && pickup < epoch + group.ready_at * 60_000)
  )
    throw new Error('Pickup must fall between the group’s recorded readiness and deadline.');
  const base = {
    group_id: group.id,
    hotel_node_id: form.hotel_node_id,
    pickup_at,
    suitcases: Number(form.suitcases),
  };
  const point: DeclaredPoint = { label, latitude, longitude, confirmed: true };
  if (returning) {
    const flight = returnFlightChoices(world, group.id, now).find(
      (item) => item.resource.id === form.onward_resource_id,
    );
    if (!flight?.eligible)
      throw new Error(
        flight?.reason ?? 'Choose a current sourced onward flight for this exact group.',
      );
    const processing = form.airport_processing_minutes ?? '';
    if (!/^\d+$/.test(processing) || Number(processing) < 1 || Number(processing) > 720)
      throw new Error('Enter an explicit operator processing allowance from 1 to 720 minutes.');
    if (
      pickup + Number(processing) * 60_000 >=
      sourceInstant(flight.resource.provenance?.departure_at)!
    )
      throw new Error(
        'Pickup must leave driving time and the declared processing allowance before the selected flight.',
      );
    return {
      ...base,
      direction: 'hotel_to_airport',
      airport_dropoff: point,
      onward_resource_id: flight.resource.id,
      airport_processing_minutes: Number(processing),
    };
  }
  return { ...base, initial_point: point };
}

export function planningQuoteCurrent(result: PlanningTransferQuote, now: number): boolean {
  const start = sourceInstant(result.created_at),
    end = sourceInstant(result.usable_until);
  return result.state === 'ready' && start !== null && end !== null && now >= start && now < end;
}
