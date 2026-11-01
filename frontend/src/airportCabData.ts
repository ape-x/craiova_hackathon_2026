import { key, request } from './api';
import { utcInputToIso } from './liveData';
import { addCalendarDays, bucharestDate } from './realData';
import { sourceInstant } from './resourceFreshness';
import type { MutationResult, PassengerGroup, World } from './types';

export const AIRPORT_CAB_SOURCE = 'https://www.bucharest.cab/en/book';
export interface AirportCabStatus {
  provider: 'Bucharest.CAB';
  configured: boolean;
  source_url: string;
  supported_routes: {
    origin_airport: string;
    origin_name: string;
    destination_airport: string;
    destination_name: string;
  }[];
  max_passengers: number;
  max_advance_days: number;
  booking_supported: false;
}
export interface AirportCabForm {
  group_id: string;
  pickup_at: string;
  suitcases: string;
  handbags: string;
}
export interface AirportCabInput {
  group_id: string;
  destination_airport: 'CRA';
  pickup_at: string;
  suitcases: number;
  handbags: number;
}
interface CabBlocker {
  code: string;
  message: string;
}
export interface AirportCabOption {
  id: string;
  provider_offer_id: null;
  provider_vehicle_type_id: number;
  car_type: string;
  price: { amount: string; currency: 'EUR' };
  price_scope: 'per_vehicle';
  max_passengers: number;
  max_suitcases: number;
  max_handbags: number;
  fits_request: boolean;
  availability_status: 'quoted_not_reserved';
  remaining_fleet: null;
  supplier_valid_until: null;
  accessibility_confirmed: false;
}
export interface AirportCabTransfer {
  provider: 'Bucharest.CAB';
  source_url: string;
  state: 'ready';
  transport: 'private_transfer';
  booking_supported: false;
  received_at: string;
  source_quoted_at: null;
  expires_at: null;
  price_basis: 'supplier_client_formula_cash_no_extras';
  request: {
    origin_airport: 'OTP';
    destination_airport: 'CRA';
    pickup_at: string;
    passengers: number;
    suitcases: number;
    handbags: number;
    timezone: 'Europe/Bucharest';
    supplier_pickup_datetime: string;
    trip_type: 'from-airport';
    baggage_supplier_scoped: false;
  };
  resolved_route: {
    origin: { airport_code: 'OTP'; name: string; address: string };
    destination: { airport_code: 'CRA'; name: string; address: string };
  };
  route: {
    distance_meters: number;
    duration_seconds: number;
    duration_text: string;
    duration_basis: 'source_estimate_without_departure_time';
  };
  options: AirportCabOption[];
  notices: string[];
}
export interface AirportCabQuote {
  quote_id: string;
  group_id: string;
  source_version: number;
  created_at: string;
  usable_until: string;
  state: 'ready' | 'blocked';
  transfer: AirportCabTransfer | null;
  options: { option_id: string; importable: boolean; blockers: CabBlocker[] }[];
  blockers: CabBlocker[];
  replayed: boolean;
}

export function airportCabGroupIssue(world: World, group?: PassengerGroup): string | null {
  if (world.mode !== 'live' || !group) return 'Choose an existing group in the live operation.';
  if (group.origin !== 'OTP') return 'This source supports groups currently at OTP.';
  if (
    group.status === 'confirmed' ||
    world.offers.some((offer) => offer.group_id === group.id && offer.status === 'confirmed')
  )
    return 'This group has recorded bookings.';
  if (
    !Number.isInteger(group.size) ||
    group.size < 1 ||
    group.size > 7 ||
    group.adult_count !== group.size
  )
    return 'This source supports 1–7 explicitly recorded adults.';
  if (group.accessible) return 'Accessible vehicle suitability is not confirmed by this source.';
  return null;
}

export function airportCabDefaultPickup(
  world: World,
  group: PassengerGroup,
  now = Date.now(),
): string {
  const ready = Date.parse(world.epoch) + (group.ready_at ?? 0) * 60_000;
  return new Date(
    Math.ceil(Math.max(now + 24 * 3_600_000, Number.isFinite(ready) ? ready : 0) / 60_000) * 60_000,
  )
    .toISOString()
    .slice(0, 16);
}

export function airportCabInput(
  form: AirportCabForm,
  world: World,
  status: AirportCabStatus,
  now = Date.now(),
): AirportCabInput {
  const group = world.groups.find((item) => item.id === form.group_id);
  const issue = airportCabGroupIssue(world, group);
  if (issue) throw new Error(issue);
  if (
    !status.configured ||
    !status.supported_routes.some(
      (route) => route.origin_airport === 'OTP' && route.destination_airport === 'CRA',
    )
  )
    throw new Error('The OTP → CRA source is not available.');
  if (group!.size > status.max_passengers)
    throw new Error(`This source currently supports at most ${status.max_passengers} passengers.`);
  for (const field of ['suitcases', 'handbags'] as const) {
    if (!/^\d+$/.test(form[field]) || Number(form[field]) > 7)
      throw new Error(
        `Enter an explicit ${field === 'suitcases' ? 'suitcase' : 'handbag'} count from 0 to 7.`,
      );
  }
  const pickup_at = utcInputToIso(form.pickup_at);
  const pickup = Date.parse(pickup_at);
  if (pickup < now + 9 * 3_600_000 || pickup % 60_000 !== 0)
    throw new Error(
      'The current conservative rule requires a whole-minute pickup at least 9 hours ahead. Source policy is rechecked for each quote.',
    );
  if (
    bucharestDate(new Date(pickup)) >
    addCalendarDays(bucharestDate(new Date(now)), status.max_advance_days)
  )
    throw new Error(`Choose a pickup within ${status.max_advance_days} days.`);
  const epoch = Date.parse(world.epoch);
  if (
    !Number.isFinite(epoch) ||
    !Number.isFinite(group!.deadline) ||
    pickup >= epoch + group!.deadline * 60_000 ||
    pickup < epoch + (group!.ready_at ?? 0) * 60_000
  )
    throw new Error(
      'Pickup must fall between the group’s recorded readiness and deadline, leaving time to drive.',
    );
  return {
    group_id: group!.id,
    destination_airport: 'CRA',
    pickup_at,
    suitcases: Number(form.suitcases),
    handbags: Number(form.handbags),
  };
}

export function airportCabContext(world: World, groupId: string): string {
  return JSON.stringify([
    world.scenario_id,
    world.mode,
    world.epoch,
    world.groups.find((group) => group.id === groupId) ?? null,
  ]);
}
export function airportCabCurrent(quote: AirportCabQuote, now: number): boolean {
  const start = sourceInstant(quote.created_at),
    end = sourceInstant(quote.usable_until);
  return quote.state === 'ready' && start !== null && end !== null && now >= start && now < end;
}
export class AirportCabRequestGate {
  private sequence = 0;
  begin() {
    return ++this.sequence;
  }
  accepts(token: number, startedScenario: string, currentScenario: string) {
    return token === this.sequence && startedScenario === currentScenario;
  }
  invalidate() {
    this.sequence++;
  }
}

function object(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}
function integer(value: unknown, min: number, max: number): value is number {
  return typeof value === 'number' && Number.isInteger(value) && value >= min && value <= max;
}
function text(value: unknown): value is string {
  return typeof value === 'string' && value.trim().length > 0;
}
function blockers(value: unknown): boolean {
  return (
    Array.isArray(value) &&
    value.every((item) => object(item) && text(item.code) && text(item.message))
  );
}

export function verifyAirportCabStatus(value: unknown): AirportCabStatus {
  if (
    !object(value) ||
    value.provider !== 'Bucharest.CAB' ||
    typeof value.configured !== 'boolean' ||
    value.source_url !== AIRPORT_CAB_SOURCE ||
    value.booking_supported !== false ||
    !integer(value.max_passengers, 1, 7) ||
    !integer(value.max_advance_days, 1, 31) ||
    !Array.isArray(value.supported_routes) ||
    !value.supported_routes.every(
      (route) =>
        object(route) &&
        route.origin_airport === 'OTP' &&
        route.destination_airport === 'CRA' &&
        text(route.origin_name) &&
        text(route.destination_name),
    )
  )
    throw new Error('The airport-transfer source status could not be verified.');
  return value as unknown as AirportCabStatus;
}

export function verifyAirportCabQuote(
  value: unknown,
  expected?: { input: AirportCabInput; passengers: number },
): AirportCabQuote {
  const invalid = () =>
    new Error('The airport-transfer receipt could not be verified. Import is unavailable.');
  if (
    !object(value) ||
    !text(value.quote_id) ||
    !text(value.group_id) ||
    !integer(value.source_version, 0, Number.MAX_SAFE_INTEGER) ||
    sourceInstant(value.created_at) === null ||
    sourceInstant(value.usable_until) === null ||
    Date.parse(String(value.usable_until)) <= Date.parse(String(value.created_at)) ||
    !['ready', 'blocked'].includes(String(value.state)) ||
    !blockers(value.blockers) ||
    !Array.isArray(value.options) ||
    typeof value.replayed !== 'boolean'
  )
    throw invalid();
  if (expected && value.group_id !== expected.input.group_id) throw invalid();
  const transfer = value.transfer;
  if (transfer !== null) {
    if (
      !object(transfer) ||
      transfer.provider !== 'Bucharest.CAB' ||
      transfer.source_url !== AIRPORT_CAB_SOURCE ||
      transfer.state !== 'ready' ||
      transfer.transport !== 'private_transfer' ||
      transfer.booking_supported !== false ||
      transfer.source_quoted_at !== null ||
      transfer.expires_at !== null ||
      transfer.price_basis !== 'supplier_client_formula_cash_no_extras' ||
      sourceInstant(transfer.received_at) === null ||
      !Array.isArray(transfer.notices) ||
      !transfer.notices.every(text)
    )
      throw invalid();
    const query = transfer.request,
      route = transfer.route,
      resolved = transfer.resolved_route;
    if (
      !object(query) ||
      query.origin_airport !== 'OTP' ||
      query.destination_airport !== 'CRA' ||
      query.timezone !== 'Europe/Bucharest' ||
      query.trip_type !== 'from-airport' ||
      query.baggage_supplier_scoped !== false ||
      sourceInstant(query.pickup_at) === null ||
      !text(query.supplier_pickup_datetime) ||
      !integer(query.passengers, 1, 7) ||
      !integer(query.suitcases, 0, 7) ||
      !integer(query.handbags, 0, 7) ||
      !object(route) ||
      !integer(route.distance_meters, 1, 2_000_000) ||
      !integer(route.duration_seconds, 1, 604800) ||
      !text(route.duration_text) ||
      route.duration_basis !== 'source_estimate_without_departure_time' ||
      !object(resolved) ||
      !object(resolved.origin) ||
      !object(resolved.destination) ||
      resolved.origin.airport_code !== 'OTP' ||
      resolved.destination.airport_code !== 'CRA' ||
      !text(resolved.origin.name) ||
      !text(resolved.destination.name) ||
      !text(resolved.origin.address) ||
      !text(resolved.destination.address)
    )
      throw invalid();
    if (
      expected &&
      (sourceInstant(query.pickup_at) !== sourceInstant(expected.input.pickup_at) ||
        query.passengers !== expected.passengers ||
        query.suitcases !== expected.input.suitcases ||
        query.handbags !== expected.input.handbags)
    )
      throw invalid();
    const passengers = query.passengers,
      suitcases = query.suitcases,
      handbags = query.handbags;
    if (
      !Array.isArray(transfer.options) ||
      !transfer.options.every(
        (option) =>
          object(option) &&
          text(option.id) &&
          text(option.car_type) &&
          integer(option.provider_vehicle_type_id, 1, 1_000_000_000) &&
          option.provider_offer_id === null &&
          object(option.price) &&
          option.price.currency === 'EUR' &&
          typeof option.price.amount === 'string' &&
          /^\d+(?:\.\d{1,2})?$/.test(option.price.amount) &&
          Number(option.price.amount) > 0 &&
          Number.isFinite(Number(option.price.amount)) &&
          option.price_scope === 'per_vehicle' &&
          integer(option.max_passengers, 1, 7) &&
          integer(option.max_suitcases, 0, 7) &&
          integer(option.max_handbags, 0, 7) &&
          typeof option.fits_request === 'boolean' &&
          option.fits_request ===
            (option.max_passengers >= passengers &&
              option.max_suitcases >= suitcases &&
              option.max_handbags >= handbags) &&
          option.availability_status === 'quoted_not_reserved' &&
          option.remaining_fleet === null &&
          option.supplier_valid_until === null &&
          option.accessibility_confirmed === false,
      )
    )
      throw invalid();
    if (new Set(transfer.options.map((option) => option.id)).size !== transfer.options.length)
      throw invalid();
  } else if (value.state === 'ready' || value.options.length > 0) throw invalid();
  const sourceOptions = (transfer as AirportCabTransfer | null)?.options ?? [];
  if (
    !value.options.every(
      (option) =>
        object(option) &&
        text(option.option_id) &&
        typeof option.importable === 'boolean' &&
        blockers(option.blockers) &&
        sourceOptions.some((source) => source.id === option.option_id) &&
        (!option.importable ||
          (value.state === 'ready' &&
            (option.blockers as unknown[]).length === 0 &&
            sourceOptions.find((source) => source.id === option.option_id)?.fits_request)),
    )
  )
    throw invalid();
  if (new Set(value.options.map((option) => option.option_id)).size !== value.options.length)
    throw invalid();
  return value as unknown as AirportCabQuote;
}

export const airportCabApi = {
  status: (signal?: AbortSignal) =>
    request<unknown>('/airport-transfers/status', undefined, signal).then(verifyAirportCabStatus),
  quoteAction(world: World, input: AirportCabInput) {
    const body = { ...input, expected_version: world.version, idempotency_key: key() };
    return () => request<unknown>('/airport-transfers/quotes', body);
  },
  importAction(world: World, quoteId: string, optionId: string) {
    const body = { expected_version: world.version, idempotency_key: key(), option_id: optionId };
    return () =>
      request<MutationResult>(
        `/airport-transfers/quotes/${encodeURIComponent(quoteId)}/import`,
        body,
      );
  },
};
