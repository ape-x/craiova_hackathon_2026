import type { TransferQuoteResult } from './transferQuoteData';
import type { World } from './types';
import { utcInputToIso } from './liveData';
import { sourceInstant } from './resourceFreshness';

export interface PlanningTransferForm {
  group_id: string;
  hotel_node_id: string;
  pickup_at: string;
  suitcases: string;
  label: string;
  latitude: string;
  longitude: string;
  confirmed: boolean;
}
export interface PlanningTransferInput {
  group_id: string;
  hotel_node_id: string;
  pickup_at: string;
  suitcases: number;
  initial_point: { label: string; latitude: number; longitude: number; confirmed: true };
}
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
  };
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
  if (label.length < 2 || label.length > 160 || /[\u0000-\u001f\u007f]/.test(label))
    throw new Error('Describe the initial meeting point using 2–160 characters.');
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
  if (!form.confirmed) throw new Error('Confirm where this group will be ready at pickup.');
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
  return {
    group_id: group.id,
    hotel_node_id: form.hotel_node_id,
    pickup_at,
    suitcases: Number(form.suitcases),
    initial_point: { label, latitude, longitude, confirmed: true },
  };
}

export function planningQuoteCurrent(result: PlanningTransferQuote, now: number): boolean {
  const start = sourceInstant(result.created_at),
    end = sourceInstant(result.usable_until);
  return result.state === 'ready' && start !== null && end !== null && now >= start && now < end;
}
